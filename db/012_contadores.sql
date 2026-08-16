-- 012_contadores.sql
-- Contadores que solo incrementan, para generar códigos sin colisiones.
--
-- Antes, cada código se armaba con COUNT(*)+1 sobre su tabla. Eso se rompe al borrar:
-- si eliminas una fila intermedia, el conteo baja y el siguiente código choca con uno
-- que ya existe. Un contador dedicado (que nunca reutiliza un número) resuelve eso y
-- también evita choques cuando se registran dos cosas al mismo tiempo.

CREATE TABLE IF NOT EXISTS contadores (
  entidad TEXT PRIMARY KEY,
  valor INTEGER NOT NULL DEFAULT 0
);

-- Arranca cada contador en el máximo actual, para no chocar con los códigos ya creados.
INSERT INTO contadores (entidad, valor)
VALUES
  ('recepcion',  (SELECT count(*) FROM recepciones_lote)),
  ('pez',        (SELECT count(*) FROM peces)),
  ('colonia',    (SELECT count(*) FROM colonias_seleccionadas)),
  ('subcultivo', (SELECT count(*) FROM subcultivos_petri)),
  ('extraccion', (SELECT count(*) FROM extracciones_adn)),
  ('nanodrop',   (SELECT count(*) FROM lecturas_nanodrop)),
  ('pcr',        (SELECT count(*) FROM pcr_reacciones)),
  ('gel',        (SELECT count(*) FROM geles_electroforesis)),
  ('control',    (SELECT count(*) FROM controles_positivos)),
  ('corrida',    (SELECT count(*) FROM pcr_corridas))
ON CONFLICT (entidad) DO NOTHING;
