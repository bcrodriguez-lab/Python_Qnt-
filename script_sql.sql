CREATE SCHEMA IF NOT EXISTS `capable-arbor-209819.volkvox2`
OPTIONS(
  location="US"
);

CREATE SCHEMA IF NOT EXISTS `capable-arbor-209819.Operacion_Analitica`
OPTIONS(
  location="US"
);

CREATE OR REPLACE TABLE `capable-arbor-209819.volkvox2.resultado_campana_llamada` (
  -- Identificadores (IDs)
  agent_id INT64,
  campaign_id INT64,
  customer_id STRING, -- Se usa STRING porque los IDs de cliente pueden contener letras o ceros a la izquierda
  skill_id INT64,
  conn_id INT64, -- Identificador único de conexión/llamada

  -- Información de la Agencia y Skill
  agent_name STRING,
  skill_name STRING,
  type_interaction STRING,

  -- Fechas y Tiempos
  date TIMESTAMP, -- O DATE si solo guardas año-mes-día, pero TIMESTAMP es mejor para trazabilidad
  time_min FLOAT64, -- Para permitir decimales si el cálculo viene fraccionado
  time_seg INT64,

  -- Tipificación y Códigos
  cod_act STRING, -- Códigos suelen ser alfanuméricos en telefonía
  cod_act_2 STRING,
  description_cod_act STRING,
  description_cod_act_2 STRING,
  comment STRING,

  -- Datos de la Llamada
  telephone STRING, -- Siempre STRING para evitar pérdida de ceros iniciales o problemas con caracteres (+)
  destiny STRING,
  hang_up STRING, -- Quién colgó (usualmente un valor categórico como 'Agent', 'Customer')
  cost NUMERIC -- Ideal para moneda por su precisión decimal exacta
)
PARTITION BY DATE(date) -- Optimiza costos al filtrar por fecha
CLUSTER BY agent_id, campaign_id; -- Acelera búsquedas frecuentes



CREATE TABLE IF NOT EXISTS `capable-arbor-209819.Operacion_Analitica.parametrizacion_campanas` (
  id INT64,
  nombre STRING,
  descripcion STRING,
  operacion STRING,
  tipo STRING,
  fecha_inicio DATETIME,
  consulta STRING,
  usuario STRING,
  servidor STRING
)