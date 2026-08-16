-- 014_auth_acl.sql
-- Autenticación segura, sesiones rotatorias, RBAC + ACL, grupos y auditoría.

ALTER TABLE usuarios_laboratorio
  ADD COLUMN IF NOT EXISTS password_hash TEXT,
  ADD COLUMN IF NOT EXISTS estado_cuenta TEXT NOT NULL DEFAULT 'pendiente',
  ADD COLUMN IF NOT EXISTS es_superadmin BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS debe_cambiar_password BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS cargo TEXT,
  ADD COLUMN IF NOT EXISTS avatar_uri TEXT,
  ADD COLUMN IF NOT EXISTS ultimo_login_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS ultima_actividad_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS intentos_fallidos INTEGER NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS bloqueado_hasta TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS aprobado_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS aprobado_por UUID REFERENCES usuarios_laboratorio(id_usuario) ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS password_cambiado_at TIMESTAMPTZ;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'usuarios_estado_cuenta_check'
  ) THEN
    ALTER TABLE usuarios_laboratorio
      ADD CONSTRAINT usuarios_estado_cuenta_check
      CHECK (estado_cuenta IN ('pendiente', 'activa', 'suspendida'));
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'usuarios_intentos_fallidos_check'
  ) THEN
    ALTER TABLE usuarios_laboratorio
      ADD CONSTRAINT usuarios_intentos_fallidos_check
      CHECK (intentos_fallidos >= 0);
  END IF;
END $$;

CREATE TABLE IF NOT EXISTS roles_acceso (
  id_rol UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  clave TEXT NOT NULL UNIQUE,
  nombre TEXT NOT NULL,
  descripcion TEXT,
  es_sistema BOOLEAN NOT NULL DEFAULT FALSE,
  activo BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS permisos_acceso (
  id_permiso UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  clave TEXT NOT NULL UNIQUE,
  modulo TEXT NOT NULL,
  recurso TEXT NOT NULL,
  accion TEXT NOT NULL,
  descripcion TEXT,
  tipo TEXT NOT NULL DEFAULT 'accion'
    CHECK (tipo IN ('vista', 'accion', 'endpoint', 'sistema')),
  ruta_frontend TEXT,
  metodo_http TEXT,
  patron_endpoint TEXT,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  nivel_riesgo TEXT NOT NULL DEFAULT 'normal'
    CHECK (nivel_riesgo IN ('bajo', 'normal', 'alto', 'critico')),
  activo BOOLEAN NOT NULL DEFAULT TRUE,
  es_sistema BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE permisos_acceso
  ADD COLUMN IF NOT EXISTS metadata JSONB NOT NULL DEFAULT '{}'::jsonb;

CREATE TABLE IF NOT EXISTS roles_permisos (
  id_rol UUID NOT NULL REFERENCES roles_acceso(id_rol) ON DELETE CASCADE,
  id_permiso UUID NOT NULL REFERENCES permisos_acceso(id_permiso) ON DELETE CASCADE,
  asignado_por UUID REFERENCES usuarios_laboratorio(id_usuario) ON DELETE SET NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (id_rol, id_permiso)
);

CREATE TABLE IF NOT EXISTS usuarios_roles (
  id_usuario UUID NOT NULL REFERENCES usuarios_laboratorio(id_usuario) ON DELETE CASCADE,
  id_rol UUID NOT NULL REFERENCES roles_acceso(id_rol) ON DELETE CASCADE,
  asignado_por UUID REFERENCES usuarios_laboratorio(id_usuario) ON DELETE SET NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (id_usuario, id_rol)
);

CREATE TABLE IF NOT EXISTS grupos_acceso (
  id_grupo UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  clave TEXT NOT NULL UNIQUE,
  nombre TEXT NOT NULL,
  descripcion TEXT,
  activo BOOLEAN NOT NULL DEFAULT TRUE,
  creado_por UUID REFERENCES usuarios_laboratorio(id_usuario) ON DELETE SET NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS grupos_miembros (
  id_grupo UUID NOT NULL REFERENCES grupos_acceso(id_grupo) ON DELETE CASCADE,
  id_usuario UUID NOT NULL REFERENCES usuarios_laboratorio(id_usuario) ON DELETE CASCADE,
  asignado_por UUID REFERENCES usuarios_laboratorio(id_usuario) ON DELETE SET NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (id_grupo, id_usuario)
);

CREATE TABLE IF NOT EXISTS grupos_roles (
  id_grupo UUID NOT NULL REFERENCES grupos_acceso(id_grupo) ON DELETE CASCADE,
  id_rol UUID NOT NULL REFERENCES roles_acceso(id_rol) ON DELETE CASCADE,
  asignado_por UUID REFERENCES usuarios_laboratorio(id_usuario) ON DELETE SET NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (id_grupo, id_rol)
);

CREATE TABLE IF NOT EXISTS usuarios_permisos (
  id_usuario UUID NOT NULL REFERENCES usuarios_laboratorio(id_usuario) ON DELETE CASCADE,
  id_permiso UUID NOT NULL REFERENCES permisos_acceso(id_permiso) ON DELETE CASCADE,
  efecto TEXT NOT NULL CHECK (efecto IN ('allow', 'deny')),
  asignado_por UUID REFERENCES usuarios_laboratorio(id_usuario) ON DELETE SET NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (id_usuario, id_permiso)
);

CREATE TABLE IF NOT EXISTS grupos_permisos (
  id_grupo UUID NOT NULL REFERENCES grupos_acceso(id_grupo) ON DELETE CASCADE,
  id_permiso UUID NOT NULL REFERENCES permisos_acceso(id_permiso) ON DELETE CASCADE,
  efecto TEXT NOT NULL CHECK (efecto IN ('allow', 'deny')),
  asignado_por UUID REFERENCES usuarios_laboratorio(id_usuario) ON DELETE SET NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (id_grupo, id_permiso)
);

CREATE TABLE IF NOT EXISTS sesiones_usuario (
  id_sesion UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  id_usuario UUID NOT NULL REFERENCES usuarios_laboratorio(id_usuario) ON DELETE CASCADE,
  refresh_token_hash CHAR(64) NOT NULL UNIQUE,
  familia_token UUID NOT NULL DEFAULT gen_random_uuid(),
  emitido_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  expira_at TIMESTAMPTZ NOT NULL,
  ultima_actividad_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  revocado_at TIMESTAMPTZ,
  motivo_revocacion TEXT,
  ip TEXT,
  user_agent TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE eventos_auditoria
  ADD COLUMN IF NOT EXISTS id_actor UUID REFERENCES usuarios_laboratorio(id_usuario) ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS id_sesion UUID REFERENCES sesiones_usuario(id_sesion) ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS accion TEXT,
  ADD COLUMN IF NOT EXISTS recurso TEXT,
  ADD COLUMN IF NOT EXISTS permiso TEXT,
  ADD COLUMN IF NOT EXISTS metodo_http TEXT,
  ADD COLUMN IF NOT EXISTS ruta TEXT,
  ADD COLUMN IF NOT EXISTS estado_http INTEGER,
  ADD COLUMN IF NOT EXISTS exito BOOLEAN NOT NULL DEFAULT TRUE,
  ADD COLUMN IF NOT EXISTS ip TEXT,
  ADD COLUMN IF NOT EXISTS user_agent TEXT,
  ADD COLUMN IF NOT EXISTS correlation_id UUID NOT NULL DEFAULT gen_random_uuid(),
  ADD COLUMN IF NOT EXISTS before_data JSONB,
  ADD COLUMN IF NOT EXISTS after_data JSONB,
  ADD COLUMN IF NOT EXISTS reversible BOOLEAN NOT NULL DEFAULT FALSE;

CREATE INDEX IF NOT EXISTS idx_audit_actor_fecha
  ON eventos_auditoria(id_actor, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_accion_fecha
  ON eventos_auditoria(accion, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_sesiones_usuario_activas
  ON sesiones_usuario(id_usuario, expira_at) WHERE revocado_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_grupos_miembros_usuario
  ON grupos_miembros(id_usuario);
CREATE INDEX IF NOT EXISTS idx_permisos_modulo
  ON permisos_acceso(modulo, recurso, accion);

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_roles_updated_at') THEN
    CREATE TRIGGER trg_roles_updated_at BEFORE UPDATE ON roles_acceso
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_permisos_updated_at') THEN
    CREATE TRIGGER trg_permisos_updated_at BEFORE UPDATE ON permisos_acceso
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_grupos_updated_at') THEN
    CREATE TRIGGER trg_grupos_updated_at BEFORE UPDATE ON grupos_acceso
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
  END IF;
END $$;

INSERT INTO roles_acceso (clave, nombre, descripcion, es_sistema)
VALUES
  ('administrador', 'Administrador', 'Control completo de la plataforma y su seguridad.', TRUE),
  ('tesista', 'Tesista', 'Captura y consulta del flujo experimental.', TRUE)
ON CONFLICT (clave) DO UPDATE SET
  nombre = EXCLUDED.nombre,
  descripcion = EXCLUDED.descripcion,
  es_sistema = TRUE,
  activo = TRUE;
