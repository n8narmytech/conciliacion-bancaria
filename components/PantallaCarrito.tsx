/**
 * Pantalla 2 — Carrito de ajustes.
 *
 * Muestra la diferencia que quedó y deja armar los ajustes que la
 * explican: los que el sistema sugiere solo, y los que carga el usuario
 * con su criterio contable.
 *
 * A diferencia de la versión anterior en Streamlit, el recálculo es
 * instantáneo: la conciliación se corrió una sola vez y agregar o quitar
 * un ajuste es una resta en el cliente, sin volver al servidor.
 */

"use client";

import { useMemo, useState } from "react";
import type { Ajuste, Estadisticas } from "@/lib/tipos";
import { cierraOk, pesos, pesosConSigno, TOLERANCIA_CIERRE } from "@/lib/formato";
import { Aviso, Boton, Chip, Panel } from "./ui";

export function PantallaCarrito({
  stats,
  ajustes,
  setAjustes,
  onVolver,
  onFinalizar,
  generando,
}: {
  stats: Estadisticas;
  ajustes: Ajuste[];
  setAjustes: (a: Ajuste[]) => void;
  onVolver: () => void;
  onFinalizar: () => void;
  generando: boolean;
}) {
  const [conceptoManual, setConceptoManual] = useState("");
  const [montoManual, setMontoManual] = useState("");
  const [ignoradas, setIgnoradas] = useState<Set<string>>(new Set());

  /* La diferencia bruta es la que devolvió la API sin ajustes aplicados.
     Todo el recálculo posterior ocurre acá, en memoria.

     El signo importa: el backend calcula
         saldo_banco_calculado = saldo_crm + apertura + ajustes
         diferencia            = saldo_banco_calculado - extracto
     con lo cual los ajustes SUMAN a la diferencia bruta. Un ajuste
     negativo (lo habitual: gastos, débitos pendientes) baja la
     diferencia; uno positivo la sube. */
  const diferenciaBruta = stats.diferencia_final;
  const sumaAjustes = useMemo(
    () => ajustes.reduce((acc, a) => acc + a.monto, 0),
    [ajustes],
  );
  const diferenciaActual = Math.round((diferenciaBruta + sumaAjustes) * 100) / 100;
  const cierra = cierraOk(diferenciaActual);

  const conceptosEnCarrito = new Set(ajustes.map((a) => a.concepto.toLowerCase()));
  const sugerenciasPendientes = stats.ajustes_sugeridos.filter(
    (s) =>
      !conceptosEnCarrito.has(s.concepto.toLowerCase()) &&
      !ignoradas.has(s.concepto.toLowerCase()),
  );

  const agregarManual = () => {
    const monto = parseFloat(montoManual);
    if (!conceptoManual.trim() || !Number.isFinite(monto) || Math.abs(monto) < 0.01) return;
    setAjustes([
      ...ajustes,
      { concepto: conceptoManual.trim(), monto, cantidad_mov: 0 },
    ]);
    setConceptoManual("");
    setMontoManual("");
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "1.25rem" }}>
      {/* ---- Estado de la conciliación ---- */}
      <Panel>
        <div
          style={{
            display: "flex",
            alignItems: "baseline",
            justifyContent: "space-between",
            gap: "1rem",
            flexWrap: "wrap",
          }}
        >
          <div>
            <span className="etiqueta">Diferencia residual</span>
            <div
              className="cifra"
              style={{
                fontSize: "2rem",
                fontWeight: 680,
                fontFamily: "var(--font-mono)",
                color: cierra ? "var(--ok)" : "var(--alerta)",
                lineHeight: 1.15,
                marginTop: "0.15rem",
              }}
            >
              {pesos(diferenciaActual)}
            </div>
            <p
              style={{
                margin: "0.3rem 0 0",
                fontSize: "0.82rem",
                color: "var(--tinta-suave)",
              }}
            >
              {cierra
                ? `Dentro de la tolerancia de ${pesos(TOLERANCIA_CIERRE)}.`
                : `Fuera de la tolerancia de ${pesos(TOLERANCIA_CIERRE)}. Faltan ajustes por explicar.`}
            </p>
          </div>
          <Chip tono={cierra ? "ok" : "alerta"}>
            {cierra ? "Concilia" : "Con diferencia"}
          </Chip>
        </div>
      </Panel>

      {stats.apertura_origen !== "derivada" && (
        <Aviso tono="alerta" titulo="No se pudo calcular el saldo de apertura">
          El extracto no trae el saldo de cierre ni permite deducirlo, así que el
          cálculo se hizo sin arrastre del mes anterior. Si la diferencia no
          cierra, revisá que el extracto sea el archivo completo del banco.
        </Aviso>
      )}

      {/* ---- Sugerencias automáticas ---- */}
      {sugerenciasPendientes.length > 0 && (
        <Panel
          titulo="Ajustes detectados automáticamente"
          descripcion="El sistema los encontró analizando los archivos. Revisalos antes de aceptarlos."
        >
          <div style={{ display: "flex", flexDirection: "column", gap: "0.7rem" }}>
            {sugerenciasPendientes.map((s) => (
              <div
                key={s.concepto}
                style={{
                  border: "1px solid var(--borde)",
                  borderRadius: 6,
                  padding: "0.8rem 0.95rem",
                  background: "var(--superficie-alt)",
                }}
              >
                <div
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    gap: "1rem",
                    alignItems: "baseline",
                    flexWrap: "wrap",
                  }}
                >
                  <strong style={{ fontSize: "0.9rem" }}>{s.concepto}</strong>
                  <span
                    className="cifra"
                    style={{
                      fontFamily: "var(--font-mono)",
                      fontWeight: 650,
                      fontSize: "0.95rem",
                    }}
                  >
                    {pesosConSigno(s.monto)}
                  </span>
                </div>
                <p
                  style={{
                    margin: "0.4rem 0 0.7rem",
                    fontSize: "0.8rem",
                    color: "var(--tinta-suave)",
                  }}
                >
                  {s.explicacion.replace(/\*\*/g, "")}
                </p>
                <div style={{ display: "flex", gap: "0.5rem" }}>
                  <Boton
                    variante="primario"
                    onClick={() =>
                      setAjustes([
                        ...ajustes,
                        {
                          concepto: s.concepto,
                          monto: s.monto,
                          cantidad_mov: s.cantidad_mov,
                          automatico: true,
                          movimientos: s.movimientos,
                        },
                      ])
                    }
                  >
                    Agregar al cálculo
                  </Boton>
                  <Boton
                    variante="fantasma"
                    onClick={() =>
                      setIgnoradas(
                        new Set([...ignoradas, s.concepto.toLowerCase()]),
                      )
                    }
                  >
                    Ignorar
                  </Boton>
                </div>
              </div>
            ))}
          </div>
        </Panel>
      )}

      {/* ---- Carrito ---- */}
      <Panel
        titulo={`Ajustes aplicados (${ajustes.length})`}
        descripcion="Estos son los que entran en el cálculo final."
      >
        {ajustes.length === 0 ? (
          <p style={{ margin: 0, fontSize: "0.86rem", color: "var(--tinta-suave)" }}>
            Todavía no aplicaste ningún ajuste. La diferencia de arriba es la
            bruta, sin explicar.
          </p>
        ) : (
          <div className="tabla-scroll">
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.88rem" }}>
              <tbody>
                {ajustes.map((a, i) => (
                  <tr key={`${a.concepto}-${i}`} style={{ borderBottom: "1px solid var(--borde)" }}>
                    <td style={{ padding: "0.55rem 0.4rem 0.55rem 0" }}>
                      {a.concepto}{" "}
                      {a.automatico && <Chip tono="acento">auto</Chip>}
                    </td>
                    <td
                      className="cifra"
                      style={{
                        padding: "0.55rem 0.4rem",
                        textAlign: "right",
                        fontFamily: "var(--font-mono)",
                        whiteSpace: "nowrap",
                      }}
                    >
                      {pesosConSigno(a.monto)}
                    </td>
                    <td style={{ padding: "0.55rem 0 0.55rem 0.4rem", width: 40, textAlign: "right" }}>
                      <Boton
                        variante="peligro"
                        onClick={() => setAjustes(ajustes.filter((_, j) => j !== i))}
                        title="Quitar este ajuste"
                      >
                        Quitar
                      </Boton>
                    </td>
                  </tr>
                ))}
                <tr>
                  <td style={{ padding: "0.6rem 0.4rem 0 0", fontWeight: 650 }}>
                    Total de ajustes
                  </td>
                  <td
                    className="cifra"
                    style={{
                      padding: "0.6rem 0.4rem 0",
                      textAlign: "right",
                      fontFamily: "var(--font-mono)",
                      fontWeight: 680,
                    }}
                  >
                    {pesosConSigno(sumaAjustes)}
                  </td>
                  <td />
                </tr>
              </tbody>
            </table>
          </div>
        )}
      </Panel>

      {/* ---- Ajuste manual ---- */}
      <Panel
        titulo="Agregar un ajuste manual"
        descripcion="Para lo que el sistema no detecta solo: Payway, partidas puntuales, correcciones."
      >
        <form
          onSubmit={(e) => {
            e.preventDefault();
            agregarManual();
          }}
          style={{ display: "flex", gap: "0.6rem", alignItems: "flex-end", flexWrap: "wrap" }}
        >
          <label style={{ flex: "2 1 220px", display: "flex", flexDirection: "column", gap: "0.3rem" }}>
            <span style={{ fontSize: "0.84rem", fontWeight: 550 }}>Concepto</span>
            <input
              value={conceptoManual}
              onChange={(e) => setConceptoManual(e.target.value)}
              placeholder="Ej: Payway pendiente de devolución"
              style={campoEstilo}
            />
          </label>
          <label style={{ flex: "1 1 150px", display: "flex", flexDirection: "column", gap: "0.3rem" }}>
            <span style={{ fontSize: "0.84rem", fontWeight: 550 }}>Monto</span>
            <input
              type="number"
              step="0.01"
              value={montoManual}
              onChange={(e) => setMontoManual(e.target.value)}
              placeholder="-604778.50"
              className="cifra"
              style={{ ...campoEstilo, textAlign: "right", fontFamily: "var(--font-mono)" }}
            />
          </label>
          <Boton type="submit" variante="secundario">
            Agregar
          </Boton>
        </form>
        <p style={{ margin: "0.6rem 0 0", fontSize: "0.78rem", color: "var(--tinta-suave)" }}>
          Usá monto negativo para restar del saldo calculado (lo habitual en
          gastos y débitos pendientes) y positivo para sumar.
        </p>
      </Panel>

      {/* ---- Desglose ---- */}
      <Panel
        titulo="Cómo se llega a la diferencia"
        descripcion={
          stats.apertura_origen === "derivada" && stats.saldo_crm_arranque !== null
            ? `Apertura derivada: el extracto cerró el mes anterior en ${pesos(stats.saldo_extracto_anterior ?? 0)} y el libro abre en ${pesos(stats.saldo_crm_arranque)}.`
            : undefined
        }
      >
        <div className="tabla-scroll">
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.87rem" }}>
            <tbody>
              <Fila etiqueta="Saldo según CRM" monto={stats.saldo_crm} />
              <Fila
                etiqueta={
                  stats.apertura_origen === "derivada"
                    ? "+ Saldo de apertura (derivado)"
                    : "+ Saldo de apertura"
                }
                monto={stats.saldo_apertura}
              />
              {ajustes.map((a, i) => (
                <Fila key={i} etiqueta={a.concepto} monto={a.monto} sangria />
              ))}
              <Fila
                etiqueta="= Saldo banco calculado"
                monto={stats.saldo_crm + stats.saldo_apertura + sumaAjustes}
                destacado
              />
              <Fila etiqueta="Saldo según extracto" monto={stats.saldo_extracto} />
              <Fila etiqueta="Diferencia residual" monto={diferenciaActual} final />
            </tbody>
          </table>
        </div>
      </Panel>

      {!cierra && ajustes.length > 0 && (
        <Aviso tono="alerta" titulo="Queda diferencia sin explicar">
          Podés generar el reporte igual: la diferencia queda documentada como
          residuo. Solo cargá ajustes que tengan respaldo real — un monto puesto
          para forzar el cierre esconde el problema en lugar de resolverlo.
        </Aviso>
      )}

      <div style={{ display: "flex", justifyContent: "space-between", gap: "0.8rem" }}>
        <Boton variante="fantasma" onClick={onVolver}>
          ← Volver
        </Boton>
        <Boton variante="primario" onClick={onFinalizar} disabled={generando}>
          {generando ? "Generando reporte…" : "Generar reporte →"}
        </Boton>
      </div>
    </div>
  );
}

const campoEstilo: React.CSSProperties = {
  padding: "0.5rem 0.65rem",
  border: "1px solid var(--borde-fuerte)",
  borderRadius: 5,
  background: "var(--superficie)",
  color: "var(--tinta)",
  fontSize: "0.9rem",
  width: "100%",
};

function Fila({
  etiqueta,
  monto,
  destacado,
  final,
  sangria,
}: {
  etiqueta: string;
  monto: number;
  destacado?: boolean;
  final?: boolean;
  sangria?: boolean;
}) {
  return (
    <tr
      style={{
        borderTop: final ? "2px solid var(--tinta)" : undefined,
        borderBottom: "1px solid var(--borde)",
        background: destacado ? "var(--acento-suave)" : undefined,
      }}
    >
      <td
        style={{
          padding: "0.5rem 0.4rem 0.5rem 0",
          paddingLeft: sangria ? "1rem" : 0,
          fontWeight: destacado || final ? 650 : 400,
          color: destacado ? "var(--acento)" : undefined,
        }}
      >
        {etiqueta}
      </td>
      <td
        className="cifra"
        style={{
          padding: "0.5rem 0",
          textAlign: "right",
          fontFamily: "var(--font-mono)",
          fontWeight: destacado || final ? 680 : 400,
          color: destacado ? "var(--acento)" : undefined,
          whiteSpace: "nowrap",
        }}
      >
        {pesos(monto)}
      </td>
    </tr>
  );
}
