/**
 * Tipos que devuelve la API de conciliación.
 *
 * Reflejan lo que arma `api/_serializacion.py`. Si cambia el payload del
 * backend, este archivo es el primero que hay que actualizar.
 */

export interface Banco {
  codigo: string;
  nombre: string;
  formatos: string[];
}

/** Ajuste que el sistema detecta solo a partir de los archivos. */
/** Movimiento identificado por su origen y su fila en el archivo. */
export interface ReferenciaMovimiento {
  origen: "CRM" | "BANCO";
  fila: number;
}

export interface AjusteSugerido {
  concepto: string;
  monto: number;
  explicacion: string;
  cantidad_mov: number;
  /** Movimientos que suma el ajuste; al aceptarlo quedan cubiertos. */
  movimientos?: ReferenciaMovimiento[];
}

/** Ajuste cargado en el carrito (sugerido y aceptado, o manual). */
export interface Ajuste {
  concepto: string;
  monto: number;
  cantidad_mov: number;
  /** true cuando vino de una sugerencia automática, para distinguirlo en la UI */
  automatico?: boolean;
  /** Movimientos que explica, cuando el ajuste vino de una sugerencia. */
  movimientos?: ReferenciaMovimiento[];
}

export interface Discrepancia {
  tipo: string;
  /** GRUPO: residuo entre los cargos del banco y el asiento que los agrupa. */
  origen: "CRM" | "BANCO" | "GRUPO";
  fila: number;
  fecha: string | null;
  monto: number;
  descripcion: string;
  contraparte?: string;
  /** Cómo queda cubierto este movimiento pese a no tener pareja individual. */
  cobertura: "agrupada" | "ajuste" | "ninguna";
  cobertura_detalle: string;
  cruza_meses?: boolean;
  nota?: string;
}

export interface Movimiento {
  id: number;
  fecha: string | null;
  monto: number;
  descripcion_orig: string;
  contraparte_orig?: string;
  estado: "pendiente" | "conciliado" | "revisar";
  match_id: string | null;
  grupo_concepto?: string;
}

export interface Estadisticas {
  // Saldos
  saldo_crm: number;
  saldo_apertura: number;
  total_ajustes: number;
  saldo_banco_calculado: number;
  saldo_extracto: number;
  diferencia_final: number;
  concilia_ok: boolean;

  // Metadata detectada del archivo
  formato_banco: string;
  saldo_inicial_detectado: number | null;
  saldo_final_detectado: number | null;

  // Conteos
  total_crm: number;
  total_banco: number;
  total_matches: number;
  total_discrepancias: number;
  conciliados_alta_confianza: number;
  a_revisar: number;

  ajustes_sugeridos: AjusteSugerido[];
  ajustes_manuales: Ajuste[];

  // Cobertura de los movimientos sin pareja individual
  cubiertos_agrupados: number;
  cubiertos_agrupados_monto: number;
  cubiertos_por_ajuste: number;
  cubiertos_por_ajuste_monto: number;
  sin_justificar: number;
  sin_justificar_monto: number;

  // Trazabilidad de la apertura
  saldo_crm_arranque: number | null;
  saldo_extracto_anterior: number | null;
  apertura_derivada: number | null;
  apertura_origen: "derivada" | "manual";
}

export interface RespuestaConciliacion {
  ok: boolean;
  error?: string;
  estadisticas: Estadisticas;
  discrepancias: Discrepancia[];
  crm: Movimiento[];
  banco: Movimiento[];
  excel_base64?: string;
}

/** Parámetros de una corrida de conciliación. */
export interface ParametrosConciliacion {
  banco: string;
  archivoCrm: File;
  archivoBanco: File;
  /**
   * Saldo del extracto al cierre del mes anterior: de acá se deriva la apertura.
   * Solo hace falta cuando el extracto del banco no informa su saldo de apertura
   * (es el caso de BBVA); en los demás el backend lo lee del propio archivo.
   */
  saldoExtractoAnterior?: number;
  saldoExtracto?: number;
  ajustes: Ajuste[];
  incluirExcel?: boolean;
}
