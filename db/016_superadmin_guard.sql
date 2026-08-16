-- 016_superadmin_guard.sql
-- Protege la cuenta raíz y desacopla la auditoría de los datos experimentales.

-- Si una instalación anterior conserva una cuenta administradora pero perdió la
-- bandera raíz, se recupera de forma conservadora. Una BD sin usuarios sigue
-- usando el flujo seguro de configuración inicial.
DO $$
DECLARE
  candidate UUID;
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM usuarios_laboratorio
    WHERE es_superadmin AND activo AND estado_cuenta='activa'
  ) THEN
    SELECT id_usuario INTO candidate
    FROM usuarios_laboratorio
    WHERE es_superadmin
    ORDER BY created_at
    LIMIT 1;

    IF candidate IS NULL THEN
      SELECT u.id_usuario INTO candidate
      FROM usuarios_laboratorio u
      JOIN usuarios_roles ur ON ur.id_usuario=u.id_usuario
      JOIN roles_acceso r ON r.id_rol=ur.id_rol
      WHERE r.clave='administrador' AND u.activo AND u.estado_cuenta='activa'
      ORDER BY u.created_at
      LIMIT 1;
    END IF;

    IF candidate IS NOT NULL THEN
      UPDATE usuarios_laboratorio
      SET es_superadmin=TRUE, activo=TRUE, estado_cuenta='activa'
      WHERE id_usuario=candidate;
    END IF;
  END IF;
END $$;

-- La bitácora debe sobrevivir cuando se vacían los datos experimentales.
ALTER TABLE eventos_auditoria
  DROP CONSTRAINT IF EXISTS eventos_auditoria_id_objeto_fkey;

CREATE OR REPLACE FUNCTION proteger_ultimo_superadmin()
RETURNS TRIGGER AS $$
DECLARE
  pierde_nivel BOOLEAN;
  restantes INTEGER;
BEGIN
  PERFORM pg_advisory_xact_lock(hashtextextended('fagolab:superadmin', 0));

  IF TG_OP = 'DELETE' THEN
    pierde_nivel := OLD.es_superadmin AND OLD.activo AND OLD.estado_cuenta='activa';
  ELSE
    pierde_nivel :=
      OLD.es_superadmin AND OLD.activo AND OLD.estado_cuenta='activa'
      AND NOT (NEW.es_superadmin AND NEW.activo AND NEW.estado_cuenta='activa');
  END IF;

  IF pierde_nivel THEN
    SELECT count(*)::int INTO restantes
    FROM usuarios_laboratorio
    WHERE es_superadmin AND activo AND estado_cuenta='activa'
      AND id_usuario<>OLD.id_usuario;
    IF restantes < 1 THEN
      RAISE EXCEPTION 'Debe permanecer al menos una superadministradora activa.'
        USING ERRCODE = '23514';
    END IF;
  END IF;

  IF TG_OP = 'DELETE' THEN
    RETURN OLD;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION proteger_truncado_superadmins()
RETURNS TRIGGER AS $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM usuarios_laboratorio
    WHERE es_superadmin AND activo AND estado_cuenta='activa'
  ) THEN
    RAISE EXCEPTION 'No se puede vaciar la tabla de usuarios: debe permanecer una superadministradora.'
      USING ERRCODE = '23514';
  END IF;
  RETURN NULL;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_proteger_superadmin_update ON usuarios_laboratorio;
CREATE TRIGGER trg_proteger_superadmin_update
BEFORE UPDATE OF es_superadmin, activo, estado_cuenta ON usuarios_laboratorio
FOR EACH ROW EXECUTE FUNCTION proteger_ultimo_superadmin();

DROP TRIGGER IF EXISTS trg_proteger_superadmin_delete ON usuarios_laboratorio;
CREATE TRIGGER trg_proteger_superadmin_delete
BEFORE DELETE ON usuarios_laboratorio
FOR EACH ROW EXECUTE FUNCTION proteger_ultimo_superadmin();

DROP TRIGGER IF EXISTS trg_proteger_superadmin_truncate ON usuarios_laboratorio;
CREATE TRIGGER trg_proteger_superadmin_truncate
BEFORE TRUNCATE ON usuarios_laboratorio
FOR EACH STATEMENT EXECUTE FUNCTION proteger_truncado_superadmins();

-- Los restores usan session_replication_role=replica; estas protecciones también
-- deben ejecutarse en ese modo.
ALTER TABLE usuarios_laboratorio ENABLE ALWAYS TRIGGER trg_proteger_superadmin_update;
ALTER TABLE usuarios_laboratorio ENABLE ALWAYS TRIGGER trg_proteger_superadmin_delete;
ALTER TABLE usuarios_laboratorio ENABLE ALWAYS TRIGGER trg_proteger_superadmin_truncate;
