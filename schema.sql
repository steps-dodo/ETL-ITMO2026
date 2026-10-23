PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS suppliers (
 supplier_id TEXT PRIMARY KEY,
 name TEXT NOT NULL,
 address TEXT NOT NULL,
 phone TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS products (
 product_id TEXT PRIMARY KEY,
 name TEXT NOT NULL,
 category TEXT NOT NULL,
 supplier_id TEXT NOT NULL REFERENCES suppliers(supplier_id),
 min_stock INTEGER NOT NULL CHECK(min_stock >= 0)
);
CREATE TABLE IF NOT EXISTS inventory (
 product_id TEXT PRIMARY KEY REFERENCES products(product_id),
 location TEXT NOT NULL,
 quantity INTEGER NOT NULL CHECK(quantity >= 0)
);
CREATE VIEW IF NOT EXISTS v_all_goods AS
 SELECT p.product_id, p.name, p.category, p.supplier_id,
 s.name AS supplier_name, i.location, i.quantity, p.min_stock,
 CASE WHEN i.quantity IS NULL THEN 'нет данных'
      WHEN i.quantity = 0 THEN 'нет в наличии'
      ELSE 'в наличии' END AS stock_status
 FROM products p JOIN suppliers s ON s.supplier_id=p.supplier_id
 LEFT JOIN inventory i ON i.product_id=p.product_id;
CREATE VIEW IF NOT EXISTS v_available AS
 SELECT * FROM v_all_goods WHERE quantity > 0;
CREATE VIEW IF NOT EXISTS v_restock AS
 SELECT *, min_stock - quantity AS to_order
 FROM v_all_goods WHERE quantity < min_stock;
