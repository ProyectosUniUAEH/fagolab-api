-- 026_orquestacion.sql — la corrida deja de ser una caja negra.
--
-- Hasta ahora una corrida solo tenía estado y un texto de progreso. Para poder dibujar el
-- flujo en vivo hace falta saber, en cada momento, en qué paso va, cuánto tardó cada uno y
-- de qué naturaleza es: código determinista, consulta a un servicio externo, o el modelo
-- generando. Eso es justamente lo que hay que poder explicar.
--
--   pasos     estado de cada nodo del flujo (para el diagrama)
--   bitacora  una línea por invocación real (para el registro de actividad)

ALTER TABLE corridas_analisis
  ADD COLUMN IF NOT EXISTS pasos     JSONB NOT NULL DEFAULT '[]'::jsonb,
  ADD COLUMN IF NOT EXISTS bitacora  JSONB NOT NULL DEFAULT '[]'::jsonb;

COMMENT ON COLUMN corridas_analisis.pasos IS
  'Nodos del flujo con su naturaleza (det | ext | gen), estado y duración. Alimenta el diagrama en vivo.';
COMMENT ON COLUMN corridas_analisis.bitacora IS
  'Traza cronológica de cada herramienta invocada, con su naturaleza y duración.';
