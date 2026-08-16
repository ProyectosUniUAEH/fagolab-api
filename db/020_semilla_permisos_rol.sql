-- 020_semilla_permisos_rol.sql -- registro de concesiones automáticas por rol.
--
-- Problema que resuelve: `sync_catalog()` concedía los permisos iniciales al rol
-- `tesista` con la guardia "y el rol todavía no tiene ningún permiso". En cuanto el rol
-- recibió su primera semilla, ese INSERT quedó inerte para siempre, así que ningún
-- permiso creado después (los 33 de chat, tareas y agente) llegó nunca al equipo:
-- el módulo de colaboración quedaba invisible para todo el mundo salvo la superadmin.
--
-- Con este registro la concesión pasa a ser "una vez por permiso" en lugar de
-- "una vez por rol": cada clave se ofrece una sola vez y, si una administradora la
-- revoca después, no vuelve a aparecer sola en el siguiente arranque.

CREATE TABLE IF NOT EXISTS roles_permisos_semilla (
  id_rol         UUID NOT NULL REFERENCES roles_acceso(id_rol) ON DELETE CASCADE,
  clave_permiso  TEXT NOT NULL,
  otorgado_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (id_rol, clave_permiso)
);

COMMENT ON TABLE roles_permisos_semilla IS
  'Permisos que el sistema ya ofreció automáticamente a un rol. Evita volver a conceder lo que una administradora revocó a propósito.';

-- Backfill: se marca como "ya ofrecido" todo permiso ajeno a los módulos nuevos, de modo
-- que las revocaciones históricas se respetan. Las claves de chat/tareas/ia quedan fuera
-- del registro a propósito para que `sync_catalog()` las conceda en el próximo arranque.
INSERT INTO roles_permisos_semilla (id_rol, clave_permiso)
SELECT r.id_rol, p.clave
FROM roles_acceso r
CROSS JOIN permisos_acceso p
WHERE r.clave = 'tesista'
  AND p.clave NOT LIKE 'chat.%'
  AND p.clave NOT LIKE 'tareas.%'
  AND p.clave NOT LIKE 'ia.%'
ON CONFLICT DO NOTHING;
