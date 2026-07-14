"""
Interfaz base para todos los bancos.

Cada banco (BBVA, Santander, etc.) debe heredar de esta clase e implementar
sus métodos específicos: cómo leer sus archivos, qué patrones de matching
tiene, qué ajustes puede detectar automáticamente, etc.

El objetivo es que la lógica común esté en `nucleo/` y todo lo específico
de cada banco esté aislado en su propio módulo. Así, agregar un nuevo banco
solo requiere crear un archivo nuevo sin tocar el resto del sistema.
"""

from abc import ABC, abstractmethod
import pandas as pd


class Banco(ABC):
    """
    Clase base que define la interfaz común de todos los bancos.

    Atributos de clase (a definir en cada subclase):
        nombre:  Nombre visible del banco ("BBVA", "Santander").
        codigo:  Identificador interno ("bbva", "santander").
        formatos_esperados:  Lista de descripciones de formatos que acepta,
                             para mostrar al usuario en la UI.
    """

    nombre: str = ""
    codigo: str = ""
    formatos_esperados: "list" = []

    # -----------------------------------------------------------------
    # LECTURA DE ARCHIVOS
    # -----------------------------------------------------------------
    @abstractmethod
    def cargar_extracto(self, archivo) -> pd.DataFrame:
        """
        Lee el extracto bancario y lo devuelve normalizado.

        El DataFrame resultante debe tener estas columnas:
            - fecha (datetime)
            - monto (float, positivo=crédito, negativo=débito)
            - descripcion_orig (str)
            - referencia (str, opcional)
            - descripcion_norm (str, normalizada para matching)
            - referencia_norm (str, normalizada)
            - origen ("BANCO")
            - fila_origen (int)
            - match_id (None)
            - estado ("pendiente")

        Además, debe setear estos attrs:
            - formato: tipo de formato detectado
            - saldo_inicial_detectado: float | None
            - saldo_final_detectado: float | None
            - path_archivo: ruta al archivo original (para leer hojas auxiliares)
        """
        raise NotImplementedError

    # -----------------------------------------------------------------
    # MATCHING
    # -----------------------------------------------------------------
    @abstractmethod
    def pasadas_matching_especificas(self, crm: pd.DataFrame,
                                     banco: pd.DataFrame,
                                     matches: list) -> None:
        """
        Ejecuta las pasadas de matching específicas de este banco.

        Se ejecutan ANTES de las pasadas comunes (match exacto y con
        tolerancia). Ejemplos:
            - BBVA: agrupación de cupones VISA/MASTER por ID de lote.
            - Santander: matching COMEX (Op X + Proveedores = COB.IMPORT).

        Debe modificar los DataFrames marcando movimientos como 'conciliado'
        y agregar los matches encontrados a la lista `matches`.

        Si un banco no tiene pasadas específicas, simplemente hace `pass`.
        """
        raise NotImplementedError

    # -----------------------------------------------------------------
    # AJUSTES SUGERIDOS
    # -----------------------------------------------------------------
    @abstractmethod
    def calcular_ajustes_sugeridos(self, discrepancias,
                                   banco_df=None,
                                   crm_df=None):
        """
        Analiza los archivos y devuelve una lista de ajustes automáticos
        que el sistema puede detectar con certeza.

        Cada ajuste es un dict con:
            {
                "concepto": str,       # ej: "Liq Master pendiente"
                "monto": float,        # ej: 2152667.15
                "explicacion": str,    # texto explicativo para el usuario
                "cantidad_mov": int,   # movs que respaldan el ajuste
            }
        """
        raise NotImplementedError

    # -----------------------------------------------------------------
    # POSIBLES DÉBITOS PENDIENTES (revisión manual)
    # -----------------------------------------------------------------
    def obtener_posibles_debitos_pendientes(self, crm_df: pd.DataFrame,
                                            banco_df: pd.DataFrame) -> list:
        """
        Identifica movimientos del CRM que podrían ser Débitos pendientes
        pero requieren revisión manual (típicamente recibos huérfanos
        cuyo monto no aparece exacto en el banco).

        Por defecto usa la implementación común. Bancos con lógica
        especial pueden sobreescribirlo.
        """
        # Implementación por defecto: se define en nucleo.ajustes_comunes
        from nucleo.ajustes_comunes import posibles_debitos_pendientes_generico
        return posibles_debitos_pendientes_generico(crm_df, banco_df)

    # -----------------------------------------------------------------
    # UTILIDADES
    # -----------------------------------------------------------------
    def __repr__(self):
        return f"<Banco {self.nombre} ({self.codigo})>"
