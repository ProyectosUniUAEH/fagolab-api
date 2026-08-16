-- 011_preferencias_siembra.sql
-- Cuántas cajas se siembran por cada combinación órgano × medio.
--
-- Antes, el wizard arrancaba con un número fijo escrito en el código (5, y luego 1) que
-- no venía de ningún dato del laboratorio. Ahora se recuerda lo último que se usó: la
-- investigadora ajusta las cantidades una vez y quedan como punto de partida para los
-- siguientes peces, hasta que las vuelva a cambiar.

CREATE TABLE IF NOT EXISTS preferencias_siembra (
  id_preferencia UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  organo TEXT NOT NULL,
  medio TEXT NOT NULL,
  cajas INTEGER NOT NULL DEFAULT 1 CHECK (cajas >= 0 AND cajas <= 99),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (organo, medio)
);
