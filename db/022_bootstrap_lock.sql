-- 022_bootstrap_lock.sql
-- Token de primer arranque (estilo Jenkins / instalador Kaanbal).
-- Tras consumirlo no se puede volver a emitir ni usar /api/auth/bootstrap.

CREATE TABLE IF NOT EXISTS sistema_bootstrap (
  id SMALLINT PRIMARY KEY CHECK (id = 1),
  token_hash TEXT,
  created_at TIMESTAMPTZ,
  consumed_at TIMESTAMPTZ,
  consumed_by UUID REFERENCES usuarios_laboratorio(id_usuario) ON DELETE SET NULL
);

INSERT INTO sistema_bootstrap (id) VALUES (1)
ON CONFLICT (id) DO NOTHING;

-- Si ya existe una superadministradora, el candado queda cerrado para siempre.
UPDATE sistema_bootstrap
SET consumed_at = COALESCE(consumed_at, now()),
    token_hash = NULL
WHERE id = 1
  AND consumed_at IS NULL
  AND EXISTS (
    SELECT 1 FROM usuarios_laboratorio
    WHERE es_superadmin AND activo AND estado_cuenta = 'activa'
  );
