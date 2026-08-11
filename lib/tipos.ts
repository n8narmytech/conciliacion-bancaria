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
export interface AjusteSugerido {
  concepto: string;
  monto: number;
  explicacion: string;
  cantidad_mov: number;
}

/** Ajuste cargado en el carrito (sugerido y aceptado, o manual). */
export interface Ajuste {
  concepto: string;
  monto: number;
  cantidad_mov: number;
  /** true cuando vino de una sugerencia automática, para distinguirlo en la UI */
  automatico?: boolean;
}

export interface Discrepancia {
  tipo: string;
  origen: "CRM" | "BANCO";
  fila: number;
  fecha: string | null;
  monto: number;
  descripcion: string;
  contraparte?: string;
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
  saldoApertura: number;
  saldoExtracto?: number;
  ajustes: Ajuste[];
  incluirExcel?: boolean;
}
