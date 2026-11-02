CREATE TABLE orders (id UUID PRIMARY KEY, data JSONB);
CREATE INDEX orders_timestamp_text ON orders ((data->>'timestamp'));
-- El cast a timestamptz no es IMMUTABLE. Se conserva Q2 original;
-- este índice de texto cumple DDL pero no se presume utilizable por Q2.
