-- Bootstrap administrativo de FIS Data.
--
-- Ejecutar con un usuario administrador de PostgreSQL, por ejemplo:
--   psql -v app_password='CAMBIAR_ESTA_CONTRASENA' -f bootstrap.sql postgres
--
-- Este script crea la instancia y los permisos. Las tablas, índices y vistas
-- se crean posteriormente con `fis-data init-db` usando el usuario de la app.

\set ON_ERROR_STOP on
\if :{?app_password}
\else
  \echo 'ERROR: Debe indicar la contraseña con -v app_password=...'
  \quit 1
\endif

-- Valores por defecto; pueden sobreescribirse con -v app_role=...,
-- -v app_database=... y -v app_schema=....
\if :{?app_role}
\else
  \set app_role fis_data
\endif
\if :{?app_database}
\else
  \set app_database fis_data
\endif
\if :{?app_schema}
\else
  \set app_schema public
\endif

SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', :'app_role', :'app_password')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = :'app_role')
\gexec

-- CREATE DATABASE no puede ejecutarse dentro de una transacción. Por eso se
-- genera y ejecuta solo cuando todavía no existe.
SELECT format('CREATE DATABASE %I OWNER %I', :'app_database', :'app_role')
WHERE NOT EXISTS (
    SELECT FROM pg_database WHERE datname = :'app_database'
)
\gexec

\connect :app_database

-- El esquema public es el valor por defecto de SQLAlchemy. Para usar otro,
-- páselo con -v app_schema=... y configure search_path en la aplicación.
CREATE SCHEMA IF NOT EXISTS :"app_schema" AUTHORIZATION :"app_role";
GRANT CONNECT ON DATABASE :"app_database" TO :"app_role";
GRANT USAGE, CREATE ON SCHEMA :"app_schema" TO :"app_role";

ALTER ROLE :"app_role" IN DATABASE :"app_database"
    SET search_path TO :"app_schema";

\echo 'Bootstrap PostgreSQL completado.'
\echo 'Configure FIS_DATA_DB_* y ejecute: fis-data init-db'
