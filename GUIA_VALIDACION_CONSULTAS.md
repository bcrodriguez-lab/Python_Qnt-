# Validación de Consultas BigQuery - Guía de Campos Aceptados

## Descripción

El sistema acepta consultas BigQuery con **múltiples variaciones de nombres de columnas**. Automáticamente mapea aliases variados a campos estándar de Wolkvox.

## Campos Estándar

| Campo Estándar | Variaciones Aceptadas | Uso |
|---|---|---|
| **customer_name** | NOMBRE, nombre_cliente, customer_name, first_name, nombre_clientee | Nombre del cliente |
| **customer_last_name** | APELLIDO, last_name, apellido_cliente, surname | Apellido del cliente |
| **id_type** | TIPOID, tipo_id, tipo_documento, id_documento | Tipo de documento |
| **customer_id** | CONTACTO__C, customer_id, cliente_id, id_cliente | ID único del cliente |
| **age** | EDAD, edad, años, age_years | Edad |
| **gender** | SEXO, gender, genero__c, genero, género | Género (M/F) |
| **country** | PAIS, país, country, country_code | País |
| **state** | DEPARTAMENTO, state, depto, provincia | Departamento/Estado |
| **city** | CIUDAD, city, municipio, town | Ciudad/Municipio |
| **zone** | ZONA, zone, area, region | Zona geográfica |
| **address** | DIRECCION, dirección, street, full_address | Dirección |
| **opt1-12** | OPT1-OPT12, opcion1-12, option1-12, operado_por__c | Campos opcionales personalizados |
| **tel1-10** | TEL1-TEL10, telefono_1-10, phone1-10 | Teléfonos adicionales |
| **tel_extra** | OTROSTEL, otros_tel, extra_phone, phone_extra | Teléfono adicional |
| **email** | EMAIL, email_1, correo, correo_electronico | Correo electrónico |
| **recall_date** | DATE_RECALL, fecha_recall, recall_fecha | Fecha de recordatorio |
| **recall_telephone** | TEL_RECALL, telefono_recall, recall_tel | Teléfono de recordatorio |

## Campos Requeridos

El sistema valida que **siempre estén presentes**:
- `customer_name` (nombre del cliente)
- `customer_id` (identificador único)

Si faltan estos campos, la consulta será rechazada con un error descriptivo.

## Ejemplo 1: Tu Consulta Original

```sql
SELECT
  INITCAP(NombreCliente) AS NOMBRE,
  '' AS APELLIDO,
  '' AS TIPOID,
  Contacto__c,
  Genero__c AS SEXO,
  Ciudad AS CIUDAD,
  ROW_NUMBER() OVER (ORDER BY Saldo_Capital_cliente DESC) AS OPT2,
  Saldo_Capital_cliente AS OPT3,
  Telefono_1 AS TEL1,
  email_1 AS EMAIL,
  '' AS RECALL_INFO,
  '' AS AGENTE,
  '' AS RESULTADOREG,
  '' AS FECHAFINREG,
  '' AS LLAMADAS,
  '' AS IDCALL,
  Operado_Por__c AS OPT1,
  Barridos_Tel AS OPT5
FROM `tu_dataset.tu_tabla`
```

**Mapeo automático:**
- `NOMBRE` → `customer_name` ✓
- `Contacto__c` → `customer_id` ✓
- `SEXO` → `gender` ✓
- `CIUDAD` → `city` ✓
- `OPT1` → `opt1` ✓
- `OPT2` → `opt2` ✓
- ... y así sucesivamente

## Ejemplo 2: Con Variaciones Diferentes

```sql
SELECT
  customer_name,
  cliente_id,
  edad,
  genero,
  ciudad,
  phone1,
  email
FROM `tu_dataset.tu_tabla`
```

Funciona igual, porque el sistema acepta las variaciones.

## Ejemplo 3: Con Campos Extra

```sql
SELECT
  NOMBRE,
  Contacto__c,
  CIUDAD,
  EMAIL,
  campo_extra_1,      -- Ignorado (no es un campo estándar)
  otra_columna,       -- Ignorada también
  TEL1
FROM `tu_dataset.tu_tabla`
```

El sistema **ignora** `campo_extra_1` y `otra_columna` porque no están en el mapeo.
Solo usa los campos que reconoce.

## Validación Automática

### ✓ Consultas Aceptadas
- [x] Tiene `customer_name` (con cualquier variación)
- [x] Tiene `customer_id` (con cualquier variación)
- [x] Puede tener campos extra (se ignoran)
- [x] Puede tener cualquier combinación de campos opcionales

### ✗ Consultas Rechazadas
- [x] Falta `customer_name`
- [x] Falta `customer_id`
- [x] No es una consulta SELECT o WITH
- [x] La consulta está vacía

## API de Validación

### Endpoint: `POST /auto-campaigns/validate-query-fields`

**Solicitud:**
```json
{
  "query": "SELECT NOMBRE, Contacto__c, ... FROM tabla"
}
```

**Respuesta (Exitosa):**
```json
{
  "success": true,
  "message": "Consulta válida.",
  "detected_columns": ["NOMBRE", "Contacto__c", "CIUDAD", ...],
  "column_mapping": {
    "NOMBRE": "customer_name",
    "Contacto__c": "customer_id",
    "CIUDAD": "city",
    ...
  },
  "sample_row": {
    "NOMBRE": "Juan Pérez",
    "Contacto__c": "12345",
    ...
  }
}
```

**Respuesta (Error):**
```json
{
  "success": false,
  "message": "Validación de consulta fallida: Faltan campos requeridos en la consulta: customer_id",
  "error": "Faltan campos requeridos..."
}
```

## Tips y Mejores Prácticas

1. **Case-insensitive**: `NOMBRE`, `nombre`, `Nombre` funcionan igual
2. **Con guiones/guiones bajos**: `nombre_cliente`, `nombre-cliente`, `nombrecliente` se normalizan
3. **Alias SQL**: Usa `AS` para claridad:
   ```sql
   SELECT NombreCliente AS NOMBRE, ...
   ```
4. **Prueba primero**: Usa el endpoint `/auto-campaigns/validate-query-fields` para validar antes de guardar
5. **Campos opcionales**: No necesitas todos los campos opt1-opt12, solo los que uses
6. **Ordena tu consulta**: Es recomendable ordenar por cantidad de dinero u otro criterio:
   ```sql
   SELECT ... ORDER BY Saldo_Capital_cliente DESC
   ```

## Resolución de Errores

### Error: "Faltan campos requeridos: customer_name"
**Solución:** Asegúrate que tu consulta incluya el nombre del cliente con cualquiera de estas variaciones:
- `NOMBRE`
- `customer_name`
- `nombre_cliente`
- `first_name`

### Error: "Faltan campos requeridos: customer_id"
**Solución:** Incluye el identificador único con cualquiera de estas variaciones:
- `Contacto__c`
- `customer_id`
- `cliente_id`
- `id_cliente`

### Mensaje: "Advertencia: la consulta no devuelve columnas compatibles"
**Solución:** Significa que los campos están con nombres no reconocidos. Verifica la ortografía o usa el endpoint de validación para ver el mapeo.

## Contacto y Soporte

Si tienes problemas con una consulta específica, copia la respuesta del validador y contacta al equipo de soporte con:
- Tu consulta SQL
- Respuesta del endpoint `/auto-campaigns/validate-query-fields`
- El error específico
