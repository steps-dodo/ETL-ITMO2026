"""Проверки в изолированных временных каталогах. Исходные файлы не меняются."""
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from etl import ROOT, clean, run


class EtlTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.data = self.root / 'data'
        self.out = self.root / 'results'
        shutil.copytree(ROOT / 'data', self.data)

    def tearDown(self):
        self.tmp.cleanup()

    def query(self, sql):
        with sqlite3.connect(self.out / 'goods.db') as conn:
            return conn.execute(sql).fetchall()

    def test_01_expected_results(self):
        result = run(self.data, self.out)
        self.assertEqual(result['read'], {'suppliers': 3, 'products': 13, 'inventory': 17})
        self.assertEqual(result['loaded'], {'suppliers': 3, 'products': 12, 'inventory': 11})
        self.assertEqual(result['rejected'], 7)
        self.assertEqual(result['outputs'], {'all_goods': 12, 'available': 9, 'restock': 7, 'by_supplier': 4})
        self.assertEqual(result['restock_units'], 18)
        self.assertEqual(self.query('SELECT product_id,to_order FROM v_restock ORDER BY product_id'),
                         [('P001',7),('P003',4),('P004',1),('P005',2),('P006',1),('P007',2),('P009',1)])
        self.assertEqual(self.query('PRAGMA foreign_key_check'), [])
        self.assertEqual(self.query('PRAGMA integrity_check'), [('ok',)])

    def test_02_repeat_run(self):
        first = run(self.data, self.out)
        before = self.query('SELECT * FROM v_all_goods ORDER BY product_id')
        csv_before = {p.name:p.read_bytes() for p in self.out.glob('*.csv')}
        self.assertEqual(first, run(self.data, self.out))
        self.assertEqual(before, self.query('SELECT * FROM v_all_goods ORDER BY product_id'))
        self.assertEqual(csv_before, {p.name:p.read_bytes() for p in self.out.glob('*.csv')})

    def test_03_missing_stock_is_not_zero(self):
        run(self.data, self.out)
        self.assertEqual(self.query("SELECT quantity,stock_status FROM v_all_goods WHERE product_id='P012'"), [(None,'нет данных')])
        self.assertEqual(self.query("SELECT * FROM v_restock WHERE product_id='P012'"), [])

    def test_04_missing_source_preserves_database(self):
        run(self.data, self.out)
        before = (self.out / 'goods.db').read_bytes()
        (self.data / 'inventory.csv').unlink()
        with self.assertRaises(FileNotFoundError):
            run(self.data, self.out)
        self.assertEqual(before, (self.out / 'goods.db').read_bytes())

    def test_05_invalid_schema_preserves_database(self):
        run(self.data, self.out)
        before = (self.out / 'goods.db').read_bytes()
        (self.data / 'inventory.csv').write_text('product_id,quantity\nP001,3\n', encoding='utf-8')
        with self.assertRaises(ValueError):
            run(self.data, self.out)
        self.assertEqual(before, (self.out / 'goods.db').read_bytes())

    def test_06_changed_snapshot_updates_stock(self):
        run(self.data, self.out)
        file = self.data / 'inventory.csv'
        file.write_text(file.read_text(encoding='utf-8').replace('P001, A-01 ,3','P001, A-01 ,10').replace('P001,A-01,3','P001,A-01,10'), encoding='utf-8')
        result = run(self.data, self.out)
        self.assertEqual(result['restock_units'], 11)
        self.assertEqual(result['outputs']['restock'], 6)
        self.assertEqual(self.query("SELECT quantity FROM inventory WHERE product_id='P001'"), [(10,)])

    def test_07_conflicting_keys_are_rejected(self):
        data = pd.DataFrame([['P001','A-01','3'],['P001','A-01','9'],['P002','A-02','0']], columns=['product_id','location','quantity'])
        errors = []
        records = clean(data, 'inventory', errors, {'P001','P002'})
        self.assertEqual(records, [{'product_id':'P002','location':'A-02','quantity':0}])
        self.assertEqual(len(errors), 2)
        self.assertTrue(all('конфликт' in r['reason'] for r in errors))

    def test_08_no_valid_stock_preserves_database(self):
        run(self.data, self.out)
        before = (self.out / 'goods.db').read_bytes()
        (self.data / 'inventory.csv').write_text('product_id,location,quantity\nP001,A-01,-1\n', encoding='utf-8')
        with self.assertRaises(ValueError):
            run(self.data, self.out)
        self.assertEqual(before, (self.out / 'goods.db').read_bytes())


if __name__ == '__main__':
    unittest.main(verbosity=2)
