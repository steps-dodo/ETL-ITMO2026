"""Учебный ETL: Excel + CSV -> проверка -> SQLite -> четыре CSV-выборки."""
import argparse
import json
import logging
import sqlite3
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
FIELDS = {
    'suppliers': ['supplier_id', 'name', 'address', 'phone'],
    'products': ['product_id', 'name', 'category', 'supplier_id', 'min_stock'],
    'inventory': ['product_id', 'location', 'quantity'],
}


def integer(value):
    """Пустое значение не считается нулём; дроби и отрицательные числа запрещены."""
    try:
        n = Decimal(value.replace(',', '.'))
        if not n.is_finite() or n < 0 or n != n.to_integral_value() or n > 2147483647:
            return None
        return int(n)
    except (InvalidOperation, ValueError):
        return None


def clean(df, table, rejects, allowed=None):
    fields = FIELDS[table]
    missing = set(fields) - set(df.columns)
    if missing:
        raise ValueError(f'{table}: отсутствуют столбцы {sorted(missing)}')
    valid = []
    def reject(row, reason):
        rejects.append({'source': table, 'row': row['_row'], 'reason': reason,
                        'record': json.dumps({k: row[k] for k in fields}, ensure_ascii=False)})
    for idx, source in df[fields].iterrows():
        row = {k: str(source[k]).strip() for k in fields}
        row['_row'] = int(idx) + 2  # Номер строки в исходном файле с заголовком.
        for key in fields:
            if key.endswith('_id'):
                row[key] = row[key].upper()
        if any(not row[k] for k in fields):
            reject(row, 'пустое обязательное поле')
            continue
        number = 'min_stock' if table == 'products' else 'quantity' if table == 'inventory' else None
        if number:
            n = integer(row[number])
            if n is None:
                reject(row, f'{number}: требуется целое неотрицательное число')
                continue
            row[number] = n
        fk = 'supplier_id' if table == 'products' else 'product_id'
        if allowed is not None and row[fk] not in allowed:
            reject(row, f'неизвестный {fk}')
            continue
        valid.append(row)
    # Точные повторы удаляются; при разных значениях одного ключа
    # отклоняется вся конфликтующая группа, без произвольного выбора записи.
    key = fields[0]
    groups = {}
    for row in valid:
        groups.setdefault(row[key], []).append(row)
    result = []
    for rows in groups.values():
        if len({tuple(r[k] for k in fields) for r in rows}) > 1:
            for row in rows:
                reject(row, 'конфликт записей с одинаковым ключом')
        else:
            result.append({k: rows[0][k] for k in fields})
            for row in rows[1:]:
                reject(row, 'точный дубликат')
    return result


def run(data_dir=ROOT / 'data', output_dir=ROOT / 'results', supplier='S01'):
    data_dir, output_dir = Path(data_dir), Path(output_dir)
    supplier = supplier.strip().upper()
    raw = {
        'suppliers': pd.read_excel(data_dir / 'catalog.xlsx', sheet_name='suppliers', dtype=str, keep_default_na=False),
        'products': pd.read_excel(data_dir / 'catalog.xlsx', sheet_name='products', dtype=str, keep_default_na=False),
        'inventory': pd.read_csv(data_dir / 'inventory.csv', dtype=str, keep_default_na=False),
    }
    rejects = []
    suppliers = clean(raw['suppliers'], 'suppliers', rejects)
    products = clean(raw['products'], 'products', rejects, {r['supplier_id'] for r in suppliers})
    inventory = clean(raw['inventory'], 'inventory', rejects, {r['product_id'] for r in products})
    if not suppliers or not products or not inventory:
        raise ValueError('После проверки одна из таблиц пуста. Предыдущая БД сохранена.')
    if supplier not in {r['supplier_id'] for r in suppliers}:
        raise ValueError(f'Поставщик {supplier} отсутствует в проверенном справочнике')
    output_dir.mkdir(parents=True, exist_ok=True)
    # Полная замена текущего снимка в одной транзакции. История не ведётся.
    with sqlite3.connect(output_dir / 'goods.db') as conn:
        conn.executescript((ROOT / 'schema.sql').read_text(encoding='utf-8'))
        with conn:
            for table in ['inventory', 'products', 'suppliers']:
                conn.execute(f'DELETE FROM {table}')
            for table, rows in [('suppliers', suppliers), ('products', products), ('inventory', inventory)]:
                fields = FIELDS[table]
                marks = ','.join('?' for _ in fields)
                conn.executemany(f'INSERT INTO {table} ({",".join(fields)}) VALUES ({marks})',
                                 [tuple(r[k] for k in fields) for r in rows])
        outputs = {}
        for name, sql, params in [
            ('all_goods', 'SELECT * FROM v_all_goods ORDER BY product_id', ()),
            ('available', 'SELECT * FROM v_available ORDER BY product_id', ()),
            ('restock', 'SELECT * FROM v_restock ORDER BY product_id', ()),
            ('by_supplier', 'SELECT * FROM v_all_goods WHERE supplier_id=? ORDER BY product_id', (supplier,)),
        ]:
            frame = pd.read_sql_query(sql, conn, params=params)
            for col in ['quantity', 'min_stock', 'to_order']:
                if col in frame:
                    frame[col] = frame[col].astype('Int64')
            frame.to_csv(output_dir / f'{name}.csv', index=False, encoding='utf-8-sig')
            outputs[name] = len(frame)
        units = conn.execute('SELECT SUM(to_order) FROM v_restock').fetchone()[0] or 0
        unknown = conn.execute('SELECT COUNT(*) FROM v_all_goods WHERE quantity IS NULL').fetchone()[0]
    pd.DataFrame(rejects, columns=['source', 'row', 'reason', 'record']).to_csv(
        output_dir / 'rejected.csv', index=False, encoding='utf-8-sig')
    summary = {
        'read': {k: len(v) for k, v in raw.items()},
        'loaded': {'suppliers': len(suppliers), 'products': len(products), 'inventory': len(inventory)},
        'rejected': len(rejects), 'outputs': outputs, 'selected_supplier': supplier,
        'restock_units': units, 'unknown_stock': unknown,
    }
    (output_dir / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    logging.info('EXTRACT: %s', summary['read'])
    logging.info('TRANSFORM: отклонено %d строк', len(rejects))
    logging.info('LOAD: %s', summary['loaded'])
    logging.info('REPORTS: %s; пополнить %d шт.; неизвестный остаток: %d', outputs, units, unknown)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, default=ROOT / 'data')
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'results')
    parser.add_argument('--supplier', default='S01')
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s', handlers=[
        logging.StreamHandler(), logging.FileHandler(args.output_dir / 'run.log', mode='w', encoding='utf-8')])
    try:
        run(args.data_dir, args.output_dir, args.supplier)
    except Exception:
        logging.exception('ETL завершился с ошибкой')
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
