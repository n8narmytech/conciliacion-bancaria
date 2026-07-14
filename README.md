# Sistema de Conciliación Bancaria

Sistema interno de ARMY TECHNOLOGIES S.A. que asiste el proceso manual de conciliación bancaria entre el CRM (libro mayor GBP) y los extractos bancarios.

## ¿Qué hace?

Automatiza tareas repetitivas de la conciliación:

- **Lee automáticamente** los archivos del CRM y del banco (BBVA / Santander).
- **Detecta saldos** iniciales y finales, formato de archivo, y tipo de movimientos.
- **Matchea automáticamente** movimientos entre CRM y banco por diferentes criterios (referencia exacta, monto con tolerancia, agrupaciones de cupones, operaciones COMEX, etc.).
- **Sugiere ajustes** automáticos detectados con alta confianza (Liq Master pendiente, Débitos pendientes, Dif gs bancarios).
- **Genera un reporte Excel** con el detalle completo.

El usuario carga ajustes manuales adicionales según su criterio (Payway, correcciones específicas, etc.) y el sistema calcula el residuo final.

## Arquitectura

```
├── app.py                     # Interfaz web con Streamlit
├── orquestador.py             # Punto de entrada: coordina la conciliación
├── conciliacion.py            # Sistema original (aún usado para clasificar_huerfanos)
├── bancos/                    # Perfiles específicos por banco
│   ├── __init__.py            # Registro de bancos disponibles
│   ├── base.py                # Clase abstracta Banco (interfaz)
│   ├── bbva.py                # Perfil BBVA (cupones VISA/MASTER, Dif gs bancarios)
│   └── santander.py           # Perfil Santander (COMEX, CO.CERT.VA, préstamos)
└── nucleo/                    # Lógica común a todos los bancos
    ├── utilidades.py          # Helpers (parseo, normalización)
    ├── carga_crm.py           # Lectura del libro mayor GBP
    ├── matching_base.py       # Pasadas de matching genérico
    └── ajustes_comunes.py     # Detección de posibles débitos pendientes
```

## Requisitos

- Python 3.9 o superior
- Ver `requirements.txt` para las dependencias.

## Instalación

```bash
# Clonar el repo
git clone <URL_DEL_REPO>
cd conciliacion-bancaria

# Instalar dependencias
pip install -r requirements.txt
```

## Uso

```bash
python3 -m streamlit run app.py
```

Se abre en el navegador en `http://localhost:8501`.

## Flujo de trabajo

1. **Pantalla 1**: elegir banco (BBVA/Santander), subir CRM y extracto bancario, cargar saldos de apertura.
2. **Pantalla 2 (carrito)**: revisar sugerencias automáticas, marcar posibles débitos pendientes, cargar ajustes manuales.
3. **Pantalla 3 (resultado)**: descargar el Excel con la conciliación completa.

## Datos de referencia

**BBVA**:
- Saldo apertura histórico: `$4.374.372,97`
- Residuo estructural esperado: `~$941`
- Ajustes típicos: Payway, Débitos pendientes, Liq Master, Dif gs bancarios

**Santander**:
- Saldo apertura histórico: `$13.983.219,82`
- Residuo estructural esperado: `~$131,87`
- Patrones específicos: COMEX (Op X + Proveedores = COB.IMPORT), CO.CERT.VA agrupadas, cuotas de préstamo

## Consultas pendientes

Cosas a definir con contabilidad:

- Santander: cómo distinguir automáticamente los 3 tipos de "- Proveedores" (COMEX / préstamo / gastos bancarios) sin la contracuenta.
- Santander: corrección del asiento del 31/05 (tiene error de $241.620,66).
- BBVA: criterio Dif gs bancarios mayo (arrastre o no).

## Contacto

Proyecto interno ARMY TECHNOLOGIES S.A.
