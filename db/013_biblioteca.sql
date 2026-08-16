-- 013_biblioteca.sql
-- Biblioteca de bibliografía: artículos de fagoterapia en PDF con sus datos.
--
-- Guarda las referencias que la doctora ha usado, las del artículo en curso, y los
-- artículos ya publicados por la doctora y sus tesistas. Cada entrada apunta a un PDF
-- guardado en la carpeta _biblioteca del servidor.

CREATE TABLE IF NOT EXISTS biblioteca (
  id_documento UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  titulo TEXT NOT NULL,
  autores TEXT,
  anio INTEGER,
  fuente TEXT,                 -- revista / congreso / editorial
  categoria TEXT NOT NULL DEFAULT 'referencia_doctora',
  notas TEXT,
  doi TEXT,
  archivo_uri TEXT,            -- /biblioteca/<archivo>.pdf  (NULL si es solo referencia)
  archivo_nombre TEXT,         -- nombre original del PDF
  size_bytes BIGINT,
  agregado_por TEXT DEFAULT 'Pamela',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_biblioteca_categoria ON biblioteca (categoria);
