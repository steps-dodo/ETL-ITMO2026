"""Просмотр результатов SQLite в терминале без дополнительной программы."""
import argparse
import sqlite3
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent


def show(conn, title, sql, params=()):
    frame = pd.read_sql_query(sql, conn, params=params)
    for col in ['Остаток', 'Минимум', 'Заказать']:
        if col in frame:
            frame[col] = frame[col].astype('Int64')
    print('\n' + title)
    frame = frame.astype(object).where(frame.notna(), 'нет данных')
    print(frame.to_string(index=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--view', choices=['all', 'available', 'restock', 'supplier', 'errors', 'summary'], default='summary')
    parser.add_argument('--supplier', default='S01')
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'results')
    args = parser.parse_args()
    db = args.output_dir / 'goods.db'
    if not db.exists():
        raise SystemExit('База не найдена. Сначала выполните python etl.py')
    if args.view == 'errors':
        frame = pd.read_csv(args.output_dir / 'rejected.csv', encoding='utf-8-sig')
        print(frame[['source', 'row', 'reason']].to_string(index=False))
        return
    # mode=ro не создаёт новую БД при ошибке в пути и запрещает запись.
    with sqlite3.connect(db.resolve().as_uri() + '?mode=ro', uri=True) as conn:
        if args.view == 'summary':
            show(conn, 'Количество записей в БД', "SELECT 'suppliers' AS Таблица, COUNT(*) AS Строк FROM suppliers UNION ALL SELECT 'products', COUNT(*) FROM products UNION ALL SELECT 'inventory', COUNT(*) FROM inventory")
            show(conn, 'Итоговые выборки', "SELECT 'Все товары' AS Выборка, COUNT(*) AS Строк FROM v_all_goods UNION ALL SELECT 'В наличии', COUNT(*) FROM v_available UNION ALL SELECT 'Для пополнения', COUNT(*) FROM v_restock UNION ALL SELECT 'Неизвестный остаток', COUNT(*) FROM v_all_goods WHERE quantity IS NULL")
            print('\nЦелостность БД:', conn.execute('PRAGMA integrity_check').fetchone()[0])
            print('Нарушения внешних ключей:', len(conn.execute('PRAGMA foreign_key_check').fetchall()))
            return
        fields = 'product_id AS Код, name AS Товар, quantity AS Остаток, min_stock AS Минимум'
        if args.view == 'restock':
            show(conn, 'Товары для пополнения', f'SELECT {fields}, to_order AS Заказать FROM v_restock ORDER BY product_id')
            print('Всего к пополнению, шт.:', conn.execute('SELECT COALESCE(SUM(to_order),0) FROM v_restock').fetchone()[0])
        elif args.view == 'supplier':
            sid = args.supplier.strip().upper()
            show(conn, f'Товары поставщика {sid}', f'SELECT {fields}, stock_status AS Статус FROM v_all_goods WHERE supplier_id=? ORDER BY product_id', (sid,))
        elif args.view == 'available':
            show(conn, 'Товары в наличии', f'SELECT {fields} FROM v_available ORDER BY product_id')
        else:
            show(conn, 'Все товары', f'SELECT {fields}, stock_status AS Статус FROM v_all_goods ORDER BY product_id')


if __name__ == '__main__':
    main()
