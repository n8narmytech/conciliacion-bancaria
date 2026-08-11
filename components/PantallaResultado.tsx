/**
 * Pantalla 3 — Resultado final.
 *
 * Muestra el cierre, permite descargar el Excel y deja revisar en detalle
 * lo que quedó sin explicar. El listado de discrepancias es lo que el
 * contador usa para investigar, así que se puede filtrar y ordenar.
 */

"use client";

import { useMemo, useState } from "react";
import type { Ajuste, Discrepancia, Estadisticas } from "@/lib/tipos";
import { cierraOk, fechaCorta, pesos, pesosConSigno } from "@/lib/formato";
import { Aviso, Boton, Chip, Panel } from "./ui";

type OrdenDiscrepancia = "monto" | "fecha";

export function PantallaResultado({
  stats,
  ajustes,
  discrepancias,
  nombreBanco,
  onDescargar,
  onVolverCarrito,
  onEmpezarDeNuevo,
}: {
  stats: Estadisticas;
  ajustes: Ajuste[];
  discrepancias: Discrepancia[];
  nombreBanco: string;
  onDescargar: () => void;
  onVolverCarrito: () => void;
  onEmpezarDeNuevo: () => void;
}) {
  const cierra = cierraOk(stats.diferencia_final);
  const sumaAjustes = ajustes.reduce((a, b) => a + b.monto, 0);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "1.25rem" }}>
      <Panel>
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "baseline",
            gap: "1rem",
            flexWrap: "wrap",
          }}
        >
          <div>
            <span className="etiqueta">{nombreBanco} · diferencia final</span>
            <div
              className="cifra"
              style={{
                fontSize: "2.1rem",
                fontWeight: 680,
                fontFamily: "var(--font-mono)",
                color: cierra ? "var(--ok)" : "var(--alerta)",
                lineHeight: 1.15,
                marginTop: "0.15rem",
              }}
            >
              {pesos(stats.diferencia_final)}
            </div>
          </div>
          <Chip tono={cierra ? "ok" : "alerta"}>
            {cierra ? "Conciliación cerrada" : "Con diferencia residual"}
          </Chip>
        </div>
      </Panel>

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))",
          gap: "0.8rem",
        }}
      >
        <Metrica etiqueta="Movimientos CRM" valor={stats.total_crm} />
        <Metrica etiqueta="Movimientos banco" valor={stats.total_banco} />
        <Metrica etiqueta="Conciliados" valor={stats.total_matches} tono="ok" />
        <Metrica
          etiqueta="Sin explicar"
          valor={stats.total_discrepancias}
          tono={stats.total_discrepancias > 0 ? "alerta" : "ok"}
        />
      </div>

      <Panel titulo="Cálculo final" acciones={<Boton variante="primario" onClick={onDescargar}>Descargar Excel</Boton>}>
        <div className="tabla-scroll">
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.87rem" }}>
            <tbody>
              <FilaCalculo etiqueta="Saldo según CRM" monto={stats.saldo_crm} />
              <FilaCalculo etiqueta="+ Saldo de apertura" monto={stats.saldo_apertura} />
              {ajustes.map((a, i) => (
                <FilaCalculo key={i} etiqueta={a.concepto} monto={a.monto} sangria />
              ))}
              <FilaCalculo
                etiqueta="= Saldo banco calculado"
                monto={stats.saldo_crm + stats.saldo_apertura + sumaAjustes}
                destacado
              />
              <FilaCalculo etiqueta="Saldo según extracto" monto={stats.saldo_extracto} />
              <FilaCalculo etiqueta="Diferencia final" monto={stats.diferencia_final} final />
            </tbody>
          </table>
        </div>
      </Panel>

      {!cierra && (
        <Aviso tono="alerta" titulo="Qué significa esta diferencia">
          Los {pesos(Math.abs(stats.diferencia_final))} que quedan no
          corresponden a ningún ajuste cargado. Revisá el detalle de abajo para
          identificar qué movimientos podrían explicarla.
        </Aviso>
      )}

      <TablaDiscrepancias discrepancias={discrepancias} />

      <div style={{ display: "flex", justifyContent: "space-between", gap: "0.8rem", flexWrap: "wrap" }}>
        <Boton variante="fantasma" onClick={onVolverCarrito}>
          ← Ajustar de nuevo
        </Boton>
        <Boton variante="secundario" onClick={onEmpezarDeNuevo}>
          Conciliar otro período
        </Boton>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */

function TablaDiscrepancias({ discrepancias }: { discrepancias: Discrepancia[] }) {
  const [orden, setOrden] = useState<OrdenDiscrepancia>("monto");
  const [filtroOrigen, setFiltroOrigen] = useState<"todos" | "CRM" | "BANCO">("todos");
  const [soloGrandes, setSoloGrandes] = useState(true);

  const visibles = useMemo(() => {
    let lista = [...discrepancias];
    if (filtroOrigen !== "todos") lista = lista.filter((d) => d.origen === filtroOrigen);
    if (soloGrandes) lista = lista.filter((d) => Math.abs(d.monto) >= 50000);
    lista.sort((a, b) =>
      orden === "monto"
        ? Math.abs(b.monto) - Math.abs(a.monto)
        : (a.fecha ?? "").localeCompare(b.fecha ?? ""),
    );
    return lista;
  }, [discrepancias, orden, filtroOrigen, soloGrandes]);

  return (
    <Panel
      titulo={`Movimientos sin explicar (${discrepancias.length})`}
      descripcion="Lo que no se pudo emparejar entre el CRM y el extracto."
    >
      <div
        style={{
          display: "flex",
          gap: "0.5rem",
          flexWrap: "wrap",
          marginBottom: "0.85rem",
          alignItems: "center",
        }}
      >
        <FiltroBoton activo={filtroOrigen === "todos"} onClick={() => setFiltroOrigen("todos")}>
          Todos
        </FiltroBoton>
        <FiltroBoton activo={filtroOrigen === "CRM"} onClick={() => setFiltroOrigen("CRM")}>
          Solo CRM
        </FiltroBoton>
        <FiltroBoton activo={filtroOrigen === "BANCO"} onClick={() => setFiltroOrigen("BANCO")}>
          Solo banco
        </FiltroBoton>
        <span style={{ width: 1, height: 20, background: "var(--borde)" }} />
        <FiltroBoton activo={soloGrandes} onClick={() => setSoloGrandes(!soloGrandes)}>
          Solo mayores a $50.000
        </FiltroBoton>
        <span style={{ width: 1, height: 20, background: "var(--borde)" }} />
        <FiltroBoton activo={orden === "monto"} onClick={() => setOrden("monto")}>
          Por monto
        </FiltroBoton>
        <FiltroBoton activo={orden === "fecha"} onClick={() => setOrden("fecha")}>
          Por fecha
        </FiltroBoton>
      </div>

      {visibles.length === 0 ? (
        <p style={{ margin: 0, fontSize: "0.86rem", color: "var(--tinta-suave)" }}>
          No hay movimientos que cumplan ese filtro.
        </p>
      ) : (
        <div className="tabla-scroll" style={{ maxHeight: 460, overflowY: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.83rem" }}>
            <thead>
              <tr>
                {["Origen", "Fecha", "Monto", "Descripción", "Tipo"].map((h, i) => (
                  <th
                    key={h}
                    className="etiqueta"
                    style={{
                      textAlign: i === 2 ? "right" : "left",
                      padding: "0.4rem 0.5rem",
                      borderBottom: "1px solid var(--borde-fuerte)",
                      position: "sticky",
                      top: 0,
                      background: "var(--superficie)",
                    }}
                  >
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {visibles.map((d, i) => (
                <tr key={i} style={{ borderBottom: "1px solid var(--borde)" }}>
                  <td style={{ padding: "0.42rem 0.5rem" }}>
                    <Chip tono={d.origen === "CRM" ? "acento" : "neutro"}>{d.origen}</Chip>
                  </td>
                  <td className="cifra" style={{ padding: "0.42rem 0.5rem", whiteSpace: "nowrap" }}>
                    {fechaCorta(d.fecha)}
                  </td>
                  <td
                    className="cifra"
                    style={{
                      padding: "0.42rem 0.5rem",
                      textAlign: "right",
                      fontFamily: "var(--font-mono)",
                      whiteSpace: "nowrap",
                      color: d.monto < 0 ? "var(--error)" : "var(--tinta)",
                    }}
                  >
                    {pesosConSigno(d.monto)}
                  </td>
                  <td style={{ padding: "0.42rem 0.5rem", color: "var(--tinta-media)" }}>
                    {d.descripcion || "—"}
                    {d.contraparte && (
                      <span style={{ color: "var(--tinta-suave)" }}> · {d.contraparte}</span>
                    )}
                  </td>
                  <td style={{ padding: "0.42rem 0.5rem", color: "var(--tinta-suave)", fontSize: "0.78rem" }}>
                    {d.tipo}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}

function FiltroBoton({
  activo,
  onClick,
  children,
}: {
  activo: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      style={{
        padding: "0.28rem 0.6rem",
        borderRadius: 4,
        fontSize: "0.78rem",
        fontWeight: activo ? 650 : 500,
        border: `1px solid ${activo ? "var(--acento)" : "var(--borde-fuerte)"}`,
        background: activo ? "var(--acento-suave)" : "var(--superficie)",
        color: activo ? "var(--acento)" : "var(--tinta-media)",
        cursor: "pointer",
      }}
    >
      {children}
    </button>
  );
}

function Metrica({
  etiqueta,
  valor,
  tono,
}: {
  etiqueta: string;
  valor: number;
  tono?: "ok" | "alerta";
}) {
  const color =
    tono === "ok" ? "var(--ok)" : tono === "alerta" ? "var(--alerta)" : "var(--tinta)";
  return (
    <div
      style={{
        background: "var(--superficie)",
        border: "1px solid var(--borde)",
        borderRadius: 7,
        padding: "0.75rem 0.9rem",
      }}
    >
      <span className="etiqueta">{etiqueta}</span>
      <div
        className="cifra"
        style={{
          fontSize: "1.4rem",
          fontWeight: 660,
          fontFamily: "var(--font-mono)",
          color,
          marginTop: "0.1rem",
        }}
      >
        {valor}
      </div>
    </div>
  );
}

function FilaCalculo({
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
