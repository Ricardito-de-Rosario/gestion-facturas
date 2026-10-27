(() => {
  "use strict";

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const api = "/api";
  const state = {
    period: new Date().toISOString().slice(0, 7),
    config: null,
    rows: [],
    token: null,
    editId: null,
    extractionFields: {},
    page: 0,
    pages: 0,
    previewUrl: null,
    sort: { key: "fecha", direction: -1 },
    validationTimer: null,
  };

  const fieldGroups = {
    comprobante: [
      ["fecha_emision", "Fecha de emisión", "date"],
      ["tipo_codigo", "Tipo de comprobante", "tipo"],
      ["punto_venta", "Punto de venta", "number"],
      ["numero", "Número", "number"],
      ["cae", "CAE", "text"],
      ["cae_vencimiento", "Vencimiento CAE", "date"],
    ],
    emisor: [
      ["emisor_cuit", "CUIT", "text"],
      ["emisor_razon_social", "Razón social", "text"],
      ["emisor_domicilio", "Domicilio", "text"],
      ["emisor_localidad", "Localidad", "text"],
      ["emisor_provincia", "Provincia", "provincia"],
      ["emisor_condicion_iva", "Condición frente al IVA", "condicion"],
    ],
    importes: [
      ["moneda", "Moneda (ISO)", "text"],
      ["cotizacion", "Cotización", "decimal"],
      ["condicion_venta", "Condición de venta", "text"],
      ["neto_gravado", "Neto gravado", "decimal"],
      ["no_gravado", "No gravado", "decimal"],
      ["exento", "Exento", "decimal"],
      ["total", "Total del comprobante", "decimal"],
    ],
  };

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, (char) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    })[char]);
  }

  async function request(path, options = {}) {
    const headers = new Headers(options.headers || {});
    if (options.method && options.method !== "GET") headers.set("X-Requested-With", "facturas");
    if (options.body && !(options.body instanceof FormData)) headers.set("Content-Type", "application/json");
    const response = await fetch(`${api}${path}`, { ...options, headers });
    if (!response.ok) {
      let message = `${response.status} ${response.statusText}`;
      try {
        const body = await response.json();
        message = body.error || body.mensaje || message;
        if (body.detalle && typeof body.detalle === "object") {
          message += `: ${Object.values(body.detalle).join("; ")}`;
        }
      } catch {
        // Conserva el estado HTTP si el servidor no respondió JSON.
      }
      throw new Error(message);
    }
    if (response.status === 204) return null;
    return response.headers.get("content-type")?.includes("application/json")
      ? response.json()
      : response;
  }

  function notify(message, isError = false) {
    const box = $("#carga-estado");
    box.textContent = message;
    box.classList.toggle("error", isError);
    box.classList.toggle("exito", !isError);
    box.hidden = false;
    if (!isError) window.setTimeout(() => { box.hidden = true; }, 5000);
  }

  function currentPeriod() {
    return $("#periodo").value || state.period;
  }

  function formattedMoney(value, currency = "") {
    if (value === null || value === undefined || value === "") return "—";
    const number = Number(value);
    if (!Number.isFinite(number)) return "—";
    return `${new Intl.NumberFormat("es-AR", { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(number)}${currency ? ` ${currency}` : ""}`;
  }

  function makeSelect(options, selected, placeholder = "— Seleccionar —") {
    return `<option value="">${placeholder}</option>${options.map(([value, label]) =>
      `<option value="${escapeHtml(value)}" ${String(value) === String(selected ?? "") ? "selected" : ""}>${escapeHtml(label)}</option>`
    ).join("")}`;
  }

  function renderFields(data = {}) {
    for (const [group, definitions] of Object.entries(fieldGroups)) {
      const container = $(`[data-grupo="${group}"]`);
      container.innerHTML = definitions.map(([name, label, type]) => {
        const value = data[name] ?? "";
        const fieldMeta = state.extractionFields[name];
        const confidence = fieldMeta?.confianza || "";
        let input;
        if (type === "tipo") {
          const choices = Object.entries(state.config?.catalogos?.tipos || {})
            .map(([code, item]) => [code, `${item.letra || "?"} · ${item.clase || "Comprobante"} (${code})`]);
          input = `<select name="${name}">${makeSelect(choices, value, "Sin identificar")}</select>`;
        } else if (type === "provincia") {
          const choices = (state.config?.catalogos?.provincias || []).map((x) => [x, x]);
          input = `<select name="${name}">${makeSelect(choices, value, "Sin identificar")}</select>`;
        } else if (type === "condicion") {
          const choices = (state.config?.catalogos?.condiciones_iva || []).map((x) => [x, x]);
          input = `<select name="${name}">${makeSelect(choices, value, "Sin identificar")}</select>`;
        } else {
          const inputType = type === "decimal" ? "number" : type;
          const step = type === "decimal" ? ' step="0.01"' : type === "number" ? ' step="1"' : "";
          input = `<input name="${name}" type="${inputType}"${step} value="${escapeHtml(value)}" ${type === "text" ? "maxlength=\"300\"" : ""}>`;
        }
        return `<label class="campo ${confidence ? `conf-${escapeHtml(confidence)}` : ""}"><span>${escapeHtml(label)}${confidence ? ` <small>(${escapeHtml(confidence)})</small>` : ""}</span>${input}</label>`;
      }).join("");
    }
    renderRows("iva", data.iva || []);
    renderRows("descuentos", data.descuentos || []);
    renderRows("tributos", data.tributos || []);
  }

  function rowInput(name, value, type = "text", step = null, options = null) {
    if (options) return `<select data-field="${name}">${makeSelect(options, value)}</select>`;
    return `<input data-field="${name}" type="${type}" value="${escapeHtml(value ?? "")}"${step ? ` step="${step}"` : ""}>`;
  }

  function renderRows(kind, rows) {
    const tbody = $(`#tabla-${kind} tbody`);
    const catalogs = state.config?.catalogos || {};
    tbody.innerHTML = rows.map((item) => {
      let cells;
      if (kind === "iva") {
        const rates = (catalogs.alicuotas || []).map((x) => [x, `${x}%`]);
        cells = `<td>${rowInput("alicuota", item.alicuota, "number", "0.01", rates)}</td>
          <td>${rowInput("base", item.base, "number", "0.01")}</td>
          <td>${rowInput("importe", item.importe, "number", "0.01")}</td>`;
      } else if (kind === "descuentos") {
        cells = `<td>${rowInput("descripcion", item.descripcion)}</td>
          <td>${rowInput("porcentaje", item.porcentaje, "number", "0.0001")}</td>
          <td>${rowInput("base", item.base, "number", "0.01")}</td>
          <td>${rowInput("importe", item.importe, "number", "0.01")}</td>
          <td>${rowInput("origen", item.origen || "MANUAL", "text", null, ["ITEM", "GENERAL", "MANUAL"].map((x) => [x, x]))}</td>`;
      } else {
        const types = Object.entries(catalogs.tributos || {});
        cells = `<td>${rowInput("tipo", item.tipo || "OTRO", "text", null, types)}</td>
          <td>${rowInput("descripcion", item.descripcion)}</td>
          <td>${rowInput("alicuota", item.alicuota, "number", "0.0001")}</td>
          <td>${rowInput("base", item.base, "number", "0.01")}</td>
          <td>${rowInput("importe", item.importe, "number", "0.01")}</td>`;
      }
      return `<tr>${cells}<td><button type="button" class="quitar-fila" aria-label="Quitar fila">Quitar</button></td></tr>`;
    }).join("");
  }

  function collectRows(kind) {
    return $$(`#tabla-${kind} tbody tr`).map((row) => {
      const item = {};
      $$("[data-field]", row).forEach((input) => {
        const value = input.value.trim();
        item[input.dataset.field] = value === "" ? null : value;
      });
      return item;
    }).filter((item) => Object.values(item).some((value) => value !== null));
  }

  function collectData() {
    const data = { periodo: currentPeriod() };
    $$("#form-factura [data-grupo] input, #form-factura [data-grupo] select").forEach((input) => {
      const value = input.value.trim();
      data[input.name] = value === "" ? null : value;
    });
    data.tipo_codigo = data.tipo_codigo ? Number(data.tipo_codigo) : null;
    data.iva = collectRows("iva");
    data.descuentos = collectRows("descuentos");
    data.tributos = collectRows("tributos");
    return data;
  }

  function setProblems(problems = [], duplicates = []) {
    const list = $("#lista-problemas");
    list.innerHTML = problems.length
      ? problems.map((p) => `<li class="${p.nivel === "error" ? "problema-error" : "problema-aviso"}">${escapeHtml(p.mensaje)}</li>`).join("")
      : "<li>No se detectaron problemas de coherencia.</li>";
    const warningList = $("#lista-advertencias");
    warningList.innerHTML = state.warnings?.length
      ? state.warnings.map((text) => `<li>${escapeHtml(text)}</li>`).join("")
      : "";
    const duplicate = $("#alerta-duplicado");
    duplicate.hidden = !duplicates.length;
    duplicate.innerHTML = duplicates.length
      ? `<strong>Posibles duplicados:</strong> ${duplicates.map((item) =>
        `${escapeHtml(item.comprobante)} — ${escapeHtml(item.proveedor || "")} (${escapeHtml(item.motivo)})`
      ).join("; ")}`
      : "";
    $("#lbl-aceptar").hidden = !problems.some((p) => p.nivel === "advertencia");
  }

  async function validateForm() {
    if ($("#editor").hidden) return;
    try {
      const result = await request("/validar", {
        method: "POST",
        body: JSON.stringify({ datos: collectData(), token: state.token, factura_id: state.editId }),
      });
      setProblems(result.problemas, result.duplicados);
      const derived = result.derivados || {};
      $("#derivados").textContent =
        `IVA: ${formattedMoney(derived.iva_total)} · Descuentos: ${formattedMoney(derived.descuento_total)} · ` +
        `Otros tributos: ${formattedMoney(derived.otros_tributos_total)} · Descuento: ${derived.descuento_porcentaje ?? "—"}%`;
    } catch (error) {
      $("#estado-guardado").textContent = error.message;
    }
  }

  function scheduleValidation() {
    window.clearTimeout(state.validationTimer);
    state.validationTimer = window.setTimeout(validateForm, 350);
  }

  function setPreview(page = 0) {
    if (page < 0 || page >= state.pages) return;
    state.page = page;
    $("#pag-info").textContent = `Página ${page + 1} de ${state.pages}`;
    $("#pag-ant").disabled = page === 0;
    $("#pag-sig").disabled = page === state.pages - 1;
    if (state.token) $("#visor-img").src = `${api}/temporal/${encodeURIComponent(state.token)}/pagina/${page}`;
    else if (state.editId) $("#visor-img").src = `${api}/facturas/${state.editId}/pagina/${page}`;
  }

  function openEditor({ data = {}, token = null, id = null, name = "", pages = 0, fields = {}, warnings = [], readOnly = false } = {}) {
    state.token = token;
    state.editId = id;
    state.pages = pages;
    state.extractionFields = fields;
    state.warnings = warnings;
    $("#editor").hidden = false;
    $("#zona-drop").hidden = true;
    $("#editor-titulo").textContent = id ? `Editar factura #${id}` : token ? "Revisión de los datos extraídos" : "Carga manual de factura";
    $("#editor-archivo").textContent = name;
    $("#visor").hidden = !token && !id;
    $("#alerta-solo-lectura").hidden = !readOnly;
    $("#form-factura").classList.toggle("solo-lectura", readOnly);
    $("#btn-guardar").disabled = readOnly;
    $("#aceptar-advertencias").checked = false;
    $("#texto-extraido").textContent = "";
    $("#abrir-pdf").href = token ? `${api}/temporal/${encodeURIComponent(token)}/pdf` : id ? `${api}/facturas/${id}/pdf` : "#";
    $("#abrir-pdf").hidden = !token && !id;
    renderFields(data);
    if (pages) setPreview(0);
    setProblems([], []);
    $("#editor").scrollIntoView({ behavior: "smooth", block: "start" });
    scheduleValidation();
  }

  async function loadFile(file) {
    if (!file) return;
    if (file.type !== "application/pdf" && !file.name.toLowerCase().endsWith(".pdf")) {
      notify("Seleccioná un archivo PDF.", true);
      return;
    }
    const form = new FormData();
    form.append("archivo", file);
    notify("Leyendo PDF…");
    try {
      const result = await request("/extraer", { method: "POST", body: form });
      $("#texto-extraido").textContent = result.texto || "No se pudo extraer texto del documento.";
      $("#visor-img").src = `${api}/temporal/${encodeURIComponent(result.token)}/pagina/0`;
      openEditor({
        data: result.datos,
        token: result.token,
        name: result.nombre,
        pages: result.paginas,
        fields: result.campos,
        warnings: result.advertencias,
      });
      setProblems(result.problemas, result.duplicados);
      notify(`PDF leído: ${result.paginas} página(s), método ${result.metodo}.`);
    } catch (error) {
      notify(error.message, true);
    }
  }

  async function discardEditor() {
    if (state.token) {
      try {
        await request(`/temporal/${encodeURIComponent(state.token)}`, { method: "DELETE" });
      } catch (error) {
        notify(`No se pudo descartar el PDF temporal: ${error.message}`, true);
      }
    }
    state.token = null;
    state.editId = null;
    $("#editor").hidden = true;
    $("#zona-drop").hidden = false;
    $("#input-archivo").value = "";
  }

  async function saveInvoice(event) {
    event.preventDefault();
    const button = $("#btn-guardar");
    button.disabled = true;
    try {
      const body = {
        datos: collectData(),
        token: state.token,
        aceptar_advertencias: $("#aceptar-advertencias").checked,
      };
      const path = state.editId ? `/facturas/${state.editId}` : "/facturas";
      const result = await request(path, {
        method: state.editId ? "PUT" : "POST",
        body: JSON.stringify(body),
      });
      state.token = null;
      state.editId = null;
      $("#editor").hidden = true;
      $("#zona-drop").hidden = false;
      $("#input-archivo").value = "";
      notify(`Factura ${result.id} guardada correctamente.`);
      await refresh();
    } catch (error) {
      notify(error.message, true);
      await validateForm();
    } finally {
      button.disabled = false;
    }
  }

  function getInvoiceData(row) {
    return row.datos || {};
  }

  function renderGrid() {
    const search = $("#f-buscar").value.trim().toLocaleLowerCase("es");
    const supplier = $("#f-proveedor").value.trim().toLocaleLowerCase("es");
    const type = $("#f-tipo").value;
    const status = $("#f-estado").value;
    const from = $("#f-desde").value;
    const to = $("#f-hasta").value;
    const rows = state.rows.filter((row) => {
      const data = getInvoiceData(row);
      const all = [data.emisor_razon_social, data.emisor_cuit, data.descripcion_tipo, data.comprobante,
        data.fecha_emision, data.total, row.estado_revision].join(" ").toLocaleLowerCase("es");
      return (!search || all.includes(search))
        && (!supplier || `${data.emisor_razon_social || ""} ${data.emisor_cuit || ""}`.toLocaleLowerCase("es").includes(supplier))
        && (!type || String(data.tipo_codigo || "") === type || data.tipo_letra === type)
        && (!status || row.estado_revision === status)
        && (!from || data.fecha_emision >= from)
        && (!to || data.fecha_emision <= to);
    });
    const keyMap = {
      fecha: (row) => getInvoiceData(row).fecha_emision || "",
      tipo: (row) => getInvoiceData(row).descripcion_tipo || "",
      comprobante: (row) => getInvoiceData(row).comprobante || "",
      cuit: (row) => getInvoiceData(row).emisor_cuit || "",
      proveedor: (row) => getInvoiceData(row).emisor_razon_social || "",
      total: (row) => Number(getInvoiceData(row).total || 0),
      estado: (row) => row.estado_revision || "",
    };
    const sortValue = keyMap[state.sort.key];
    if (sortValue) rows.sort((a, b) => String(sortValue(a)).localeCompare(String(sortValue(b)), "es", { numeric: true }) * state.sort.direction);
    const tbody = $("#grid tbody");
    tbody.innerHTML = rows.map((row) => {
      const d = getInvoiceData(row);
      const alicuotas = (d.iva || []).map((item) => `${item.alicuota}%`).join(", ") || "—";
      const location = [d.emisor_localidad, d.emisor_provincia].filter(Boolean).join(", ") || "—";
      const editButton = row.periodo_cerrado ? "" : `<button type="button" data-action="edit" data-id="${row.id}">Editar</button>`;
      const deleteButton = row.periodo_cerrado ? "" : `<button type="button" data-action="delete" data-id="${row.id}">Eliminar</button>`;
      const pdfButton = row.documento ? `<a href="${api}/facturas/${row.id}/pdf" target="_blank" rel="noopener">PDF</a>` : "";
      return `<tr>
        <td>${escapeHtml(d.fecha_emision || "—")}</td><td>${escapeHtml(d.descripcion_tipo || "—")}</td>
        <td>${escapeHtml(d.comprobante || "—")}</td><td>${escapeHtml(row.emisor_cuit_formateado || d.emisor_cuit || "—")}</td>
        <td>${escapeHtml(d.emisor_razon_social || "—")}</td><td>${escapeHtml(location)}</td>
        <td>${escapeHtml(d.moneda || "—")}</td><td class="num">${formattedMoney(row.firmado?.neto_gravado ?? d.neto_gravado)}</td>
        <td class="num">${formattedMoney(d.no_gravado)} / ${formattedMoney(d.exento)}</td><td>${escapeHtml(alicuotas)}</td>
        <td class="num">${formattedMoney(row.firmado?.iva_total ?? d.iva_total)}</td>
        <td class="num">${d.descuento_porcentaje == null ? "—" : `${escapeHtml(d.descuento_porcentaje)}%`}</td>
        <td class="num">${formattedMoney(row.firmado?.descuento_total ?? d.descuento_total)}</td>
        <td class="num">${formattedMoney(row.firmado?.total ?? d.total)}</td>
        <td><span class="estado ${row.estado_revision === "VERIFICADA" ? "verificada" : "pendiente"}">${escapeHtml(row.estado_revision)}</span></td>
        <td class="acciones-celda">${pdfButton}<button type="button" data-action="view" data-id="${row.id}">Ver</button>${editButton}${deleteButton}</td>
      </tr>`;
    }).join("");
    $("#grid-info").textContent = `${rows.length} factura(s) mostradas de ${state.rows.length}.`;
  }

  function renderSummary(summary) {
    $("#resumen-periodo").textContent = state.period;
    const content = $("#resumen-contenido");
    if (!summary.por_moneda?.length) {
      content.innerHTML = "<p>No hay facturas registradas para este período.</p>";
    } else {
      content.innerHTML = summary.por_moneda.map((currency) => {
        const g = currency;
        return `<article class="resumen-moneda">
          <h3>Totales en ${escapeHtml(currency.moneda)}</h3>
          <div class="tarjetas">
            ${summaryCard("Facturas", g.cantidad)}
            ${summaryCard("Pendientes de revisión", summary.pendientes)}
            ${summaryCard("Total", formattedMoney(g.total, currency.moneda))}
            ${summaryCard("Neto gravado", formattedMoney(g.neto_gravado, currency.moneda))}
            ${summaryCard("No gravado / exento", `${formattedMoney(g.no_gravado, currency.moneda)} / ${formattedMoney(g.exento, currency.moneda)}`)}
            ${summaryCard("IVA total", formattedMoney(g.iva_total, currency.moneda))}
            ${summaryCard("Descuentos", formattedMoney(g.descuentos, currency.moneda))}
            ${summaryCard("Otros tributos", formattedMoney(g.otros_tributos, currency.moneda))}
          </div>
          <h4>IVA por alícuota</h4>
          ${currency.iva_por_alicuota?.length
            ? `<ul>${currency.iva_por_alicuota.map((item) => `<li>${escapeHtml(item.alicuota)}%: ${formattedMoney(item.importe, currency.moneda)}${item.base == null ? "" : ` (base ${formattedMoney(item.base, currency.moneda)})`}</li>`).join("")}</ul>`
            : "<p>Sin IVA discriminado.</p>"}
          <h4>Totales por proveedor</h4>
          ${currency.por_proveedor?.length
            ? `<ul>${currency.por_proveedor.map((item) => `<li>${escapeHtml(item.razon_social || item.cuit || "Sin identificar")}: ${formattedMoney(item.total, currency.moneda)}</li>`).join("")}</ul>`
            : "<p>Sin proveedores.</p>"}
          <h4>Totales por tipo de comprobante</h4>
          <ul>${(currency.por_tipo || []).map((item) => `<li>${escapeHtml(item.tipo)}: ${formattedMoney(item.total, currency.moneda)}</li>`).join("")}</ul>
          <h4>Otros tributos</h4>
          <ul>${(currency.tributos_por_tipo || []).map((item) => `<li>${escapeHtml(item.descripcion)}: ${formattedMoney(item.importe, currency.moneda)}</li>`).join("") || "<li>Sin otros tributos.</li>"}</ul>
        </article>`;
      }).join("");
    }
    const closed = summary.estado === "CERRADO";
    $("#estado-periodo").textContent = closed ? "Período cerrado" : "Período abierto";
    $("#estado-periodo").className = `chip ${closed ? "cerrado" : "abierto"}`;
    $("#btn-cerrar").disabled = closed;
    $("#btn-reabrir").disabled = !closed;
    $("#btn-excel").href = `${api}/informes/${encodeURIComponent(state.period)}.xlsx`;
    $("#btn-pdf").href = `${api}/informes/${encodeURIComponent(state.period)}.pdf`;
  }

  function summaryCard(title, value) {
    return `<div class="tarjeta"><span>${escapeHtml(title)}</span><strong>${escapeHtml(value)}</strong></div>`;
  }

  async function refreshPeriods() {
    const periods = await request("/periodos");
    const select = $("#periodos-conocidos");
    select.innerHTML = `<option value="">Períodos guardados…</option>${periods.map((item) =>
      `<option value="${escapeHtml(item.clave)}">${escapeHtml(item.clave)}</option>`
    ).join("")}`;
    select.value = periods.some((item) => item.clave === state.period) ? state.period : "";
  }

  async function refresh() {
    state.period = currentPeriod();
    const all = $("#f-todos").checked;
    const query = all ? "?todos=1" : `?periodo=${encodeURIComponent(state.period)}`;
    const [rows, summary] = await Promise.all([
      request(`/facturas${query}`),
      request(`/resumen?periodo=${encodeURIComponent(state.period)}`),
    ]);
    state.rows = rows;
    renderGrid();
    renderSummary(summary);
    await refreshPeriods();
    if (all) $("#grid-info").textContent = `${state.rows.length} factura(s), todos los períodos.`;
  }

  async function editInvoice(id) {
    try {
      const row = await request(`/facturas/${id}`);
      openEditor({
        data: row.datos,
        id: row.id,
        pages: row.documento?.paginas || 0,
        name: row.documento?.nombre_original || "Sin PDF asociado",
        fields: row.campos,
        readOnly: row.periodo_cerrado,
      });
      if (row.documento) $("#texto-extraido").textContent = "Factura registrada. El PDF original puede abrirse mediante el enlace.";
      $("#visor-img").onerror = () => { $("#visor-img").removeAttribute("src"); };
    } catch (error) {
      notify(error.message, true);
    }
  }

  async function deleteInvoice(id) {
    if (!window.confirm(`¿Eliminar la factura ${id}? Esta acción no se puede deshacer.`)) return;
    try {
      await request(`/facturas/${id}`, { method: "DELETE" });
      notify(`Factura ${id} eliminada.`);
      await refresh();
    } catch (error) {
      notify(error.message, true);
    }
  }

  async function showInvoice(id) {
    try {
      const row = await request(`/facturas/${id}`);
      openEditor({
        data: row.datos,
        id: row.id,
        pages: row.documento?.paginas || 0,
        name: row.documento?.nombre_original || "Sin PDF asociado",
        fields: row.campos,
        readOnly: true,
      });
      $("#btn-cancelar").textContent = "Cerrar";
    } catch (error) {
      notify(error.message, true);
    }
  }

  async function closePeriod() {
    const period = currentPeriod();
    if (!window.confirm(`¿Cerrar el período ${period}? Podrá reabrirse indicando un motivo.`)) return;
    try {
      await request(`/periodos/${encodeURIComponent(period)}/cerrar`, { method: "POST", body: JSON.stringify({}) });
      notify(`Período ${period} cerrado.`);
      await refresh();
    } catch (error) {
      notify(error.message, true);
    }
  }

  async function reopenPeriod() {
    const period = currentPeriod();
    const motive = window.prompt(`Motivo para reabrir ${period}:`);
    if (motive === null) return;
    try {
      await request(`/periodos/${encodeURIComponent(period)}/reabrir`, {
        method: "POST",
        body: JSON.stringify({ motivo: motive }),
      });
      notify(`Período ${period} reabierto.`);
      await refresh();
    } catch (error) {
      notify(error.message, true);
    }
  }

  async function loadHistory() {
    try {
      const history = await request(`/periodos/${encodeURIComponent(currentPeriod())}/historial`);
      $("#historial-contenido").innerHTML = history.length
        ? `<ol>${history.map((item) => `<li>${escapeHtml(item.accion)} — ${escapeHtml(item.fecha)}${item.motivo ? ` — ${escapeHtml(item.motivo)}` : ""}</li>`).join("")}</ol>`
        : "<p>Sin cierres ni reaperturas para este período.</p>";
    } catch (error) {
      $("#historial-contenido").textContent = error.message;
    }
  }

  function bindEvents() {
    $("#periodo").value = state.period;
    $("#periodo").addEventListener("change", () => {
      state.period = currentPeriod();
      refresh().catch((error) => notify(error.message, true));
    });
    $("#periodos-conocidos").addEventListener("change", (event) => {
      if (!event.target.value) return;
      $("#periodo").value = event.target.value;
      state.period = event.target.value;
      refresh().catch((error) => notify(error.message, true));
    });
    $("#btn-seleccionar").addEventListener("click", () => $("#input-archivo").click());
    $("#input-archivo").addEventListener("change", (event) => loadFile(event.target.files[0]));
    $("#zona-drop").addEventListener("dragover", (event) => { event.preventDefault(); $("#zona-drop").classList.add("arrastrando"); });
    $("#zona-drop").addEventListener("dragleave", () => $("#zona-drop").classList.remove("arrastrando"));
    $("#zona-drop").addEventListener("drop", (event) => {
      event.preventDefault();
      $("#zona-drop").classList.remove("arrastrando");
      loadFile(event.dataTransfer.files[0]);
    });
    $("#zona-drop").addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        $("#input-archivo").click();
      }
    });
    $("#btn-manual").addEventListener("click", () => openEditor({ data: { periodo: currentPeriod(), moneda: "ARS" } }));
    $("#pag-ant").addEventListener("click", () => setPreview(state.page - 1));
    $("#pag-sig").addEventListener("click", () => setPreview(state.page + 1));
    $("#btn-cancelar").addEventListener("click", () => {
      $("#btn-cancelar").textContent = "Descartar";
      discardEditor();
    });
    $("#form-factura").addEventListener("submit", saveInvoice);
    $("#form-factura").addEventListener("input", scheduleValidation);
    $("#form-factura").addEventListener("change", (event) => {
      if (event.target.matches("#aceptar-advertencias")) return;
      scheduleValidation();
    });
    document.addEventListener("click", (event) => {
      const addButton = event.target.closest("[data-agregar]");
      if (addButton) {
        const kind = addButton.dataset.agregar;
        const defaults = { iva: { alicuota: "21" }, descuentos: { origen: "MANUAL" }, tributos: { tipo: "OTRO" } };
        renderRows(kind, [...collectRows(kind), defaults[kind]]);
        return;
      }
      if (event.target.closest(".quitar-fila")) {
        event.target.closest("tr").remove();
        scheduleValidation();
      }
      const action = event.target.closest("[data-action]");
      if (action) {
        const id = Number(action.dataset.id);
        if (action.dataset.action === "edit") editInvoice(id);
        if (action.dataset.action === "view") showInvoice(id);
        if (action.dataset.action === "delete") deleteInvoice(id);
      }
    });
    $$("#grid thead th[data-col]").forEach((header) => header.addEventListener("click", () => {
      const key = header.dataset.col;
      state.sort = { key, direction: state.sort.key === key ? -state.sort.direction : 1 };
      renderGrid();
    }));
    ["#f-buscar", "#f-proveedor", "#f-tipo", "#f-estado", "#f-desde", "#f-hasta"].forEach((selector) =>
      $(selector).addEventListener("input", renderGrid));
    $("#f-todos").addEventListener("change", () => refresh().catch((error) => notify(error.message, true)));
    $("#f-limpiar").addEventListener("click", () => {
      ["#f-buscar", "#f-proveedor", "#f-tipo", "#f-estado", "#f-desde", "#f-hasta"].forEach((selector) => { $(selector).value = ""; });
      $("#f-todos").checked = false;
      refresh().catch((error) => notify(error.message, true));
    });
    $("#btn-cerrar").addEventListener("click", closePeriod);
    $("#btn-reabrir").addEventListener("click", reopenPeriod);
    $("#historial").addEventListener("toggle", () => { if ($("#historial").open) loadHistory(); });
  }

  async function start() {
    bindEvents();
    try {
      state.config = await request("/configuracion");
      $("#max-mb").textContent = state.config.max_tamano_mb;
      const types = Object.entries(state.config.catalogos.tipos);
      $("#f-tipo").insertAdjacentHTML("beforeend", types.map(([code, item]) =>
        `<option value="${escapeHtml(code)}">${escapeHtml(item.letra)} · ${escapeHtml(item.clase)}</option>`
      ).join(""));
      $("#info-config").textContent = state.config.ocr_disponible
        ? "OCR disponible"
        : "OCR no disponible: los escaneos deberán revisarse manualmente";
      renderFields({});
      await refresh();
    } catch (error) {
      notify(`No se pudo iniciar la interfaz: ${error.message}`, true);
    }
  }

  document.addEventListener("DOMContentLoaded", start);
})();
