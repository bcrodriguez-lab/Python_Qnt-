(function () {
    function message(text, ok) {
        const el = document.getElementById("autoCampaignMessage");
        if (!el) {
            alert(text);
            return;        }
        el.textContent = text;
        el.className = "alert mt-3 " + (ok ? "alert-success" : "alert-danger");
    }
    
    async function parseResponse(response) {
      const text = await response.text();
        try {
            return text ? JSON.parse(text) : {};
        } catch (err) {
            return { success: false, message: text || response.statusText };
        }
    }

    function getTiposConFlujo() {
        const form = document.getElementById("autoCampaignForm");
        const raw = form?.dataset?.tiposConFlujo;
        if (!raw) {
            return [];
        }
        try {
            return JSON.parse(raw);
        } catch (err) {
            return [];
        }
    }

    function tipoRequiereFlujo(tipo) {
        if (!tipo) return false;
        const tipos = getTiposConFlujo();
        return tipos.some((t) => String(t).toLowerCase() === tipo.toLowerCase());
    }

    function setFlujoProcesoPlaceholder(text) {
        const select = document.getElementById("autoCampaignFlujoProceso");
        if (!select) return;
        select.innerHTML = "";
        const opt = document.createElement("option");
        opt.value = "";
        opt.textContent = text;
        select.appendChild(opt);
    }

    async function refreshFlujosProcesoOptions(selectedId) {
        const select = document.getElementById("autoCampaignFlujoProceso");
        const hint = document.getElementById("autoFlujoProcesoHint");
        const servidor = (document.querySelector("[name='server_name']") || {}).value || "";
        const current = selectedId || select.value || "";
        if (!servidor) {
            setFlujoProcesoPlaceholder("(Seleccione servidor primero)");
            if (hint) {
                hint.textContent = "Elija el servidor arriba para ver los flujos disponibles.";
            }
            return;
        }
        setFlujoProcesoPlaceholder("(Cargando flujos...)");
        try {
            const resp = await fetch("/config-bigquery/flujos-proceso?server=" + encodeURIComponent(servidor));
            const data = await resp.json();
            if (!resp.ok || !data.success) {
                setFlujoProcesoPlaceholder("(Error al cargar flujos)");
                return;
            }
            const flujos = data.flujos || [];
            if (!flujos.length) {
                setFlujoProcesoPlaceholder("(Sin flujos para este servidor)");
                if (hint) {
                    hint.textContent = 'No hay flujos en Configuración → Flujos de proceso para el servidor «' + servidor + '».';
                }
                return;
            }
            setFlujoProcesoPlaceholder("(Seleccionar flujo)");
            flujos.forEach((f) => {
                const opt = document.createElement("option");
                opt.value = f.id;
                opt.textContent = f.label || (f.id + ' - ' + f.nombre);
                select.appendChild(opt);
            });
            if (hint) {
                hint.textContent = "Solo Llamada y WhatsApp. Se guarda el id del flujo.";
            }
            if (current) {
                select.value = current;
            }
        } catch (err) {
            console.error(err);
            setFlujoProcesoPlaceholder("(Error al cargar flujos)");
        }
    }

    function toggleFlujoProcesoField() {
        const tipo = (document.querySelector("[name='campaign_type']") || {}).value.trim();
        const group = document.getElementById("autoFlujoProcesoGroup");
        const select = document.getElementById("autoCampaignFlujoProceso");
        const show = tipoRequiereFlujo(tipo);
        if (group) {
            group.style.display = show ? "" : "none";
        }
        if (!show && select) {
            select.value = "";
        } else if (show) {
            refreshFlujosProcesoOptions();
        }
    }

    async function postAction(id, action) {
        // "modify" descarga el reporte previo y luego navega al formulario de edición.
        if (action === 'modify') {
            if (!confirm('Se descargará el informe en Excel antes de editar. ¿Continuar?')) return;
            const response = await fetch(`/auto-campaigns/${id}/download-report-before-edit`, {
                method: 'POST',
                headers: { 'Accept': 'application/json' }
            });
            // Si falla, igual dejamos que el usuario edite.
            try {
                const data = await parseResponse(response);
                if (response.ok && data.success !== false) {
                    // OK
                } else if (data && data.message) {
                    message(data.message, false);
                }
            } catch (e) {
                // Si el endpoint devuelve binario, parseResponse fallará; en ese caso igual navegamos.
            }
            window.location.href = `/auto-campaigns/${id}`;
            return;
        }

        const map = {
            run: { url: `/auto-campaigns/${id}/run`, method: "POST", confirm: null },
            stop: { url: `/auto-campaigns/${id}/stop`, method: "POST", confirm: null },
            reset: { url: `/auto-campaigns/${id}/reset`, method: "POST", confirm: "¿Reiniciar el ciclo de esta campaña?" },
            records: { url: `/auto-campaigns/${id}/records`, method: "DELETE", confirm: "¿Borrar registros remotos si hay endpoint y eliminar logs locales?" },
            delete: { url: `/auto-campaigns/${id}/delete-report`, method: "POST", confirm: "¿Eliminar esta campaña automática? (Descargará el reporte en Excel antes de eliminar)" }
        };

        const cfg = map[action];
        if (!cfg) return;
        if (cfg.confirm && !confirm(cfg.confirm)) return;
        const response = await fetch(cfg.url, { method: cfg.method, headers: { "Accept": "application/json" } });
        const data = await parseResponse(response);
        message(data.message || (response.ok ? "Acción completada." : "Error ejecutando acción."), response.ok && data.success !== false);
        if (response.ok && ["delete", "reset", "records"].includes(action)) {
            setTimeout(() => window.location.reload(), 800);
        }
    }

    function scheduleValueForSubmit(type, value) {
        const text = (value || "").trim();
        if (type === "manual") return "";
        if (type === "recurring" && text && !text.startsWith("{")) {
            return JSON.stringify({ interval_hours: Number(text) || 24 });
        }
        return text;
    }

    async function saveForm(form) {
        const formData = new FormData(form);
        const type = formData.get("schedule_type") || "manual";
        const payload = {
            name: (formData.get("name") || "").trim(),
            operation: (formData.get("operation") || "").trim(),
            type: (formData.get("type") || "").trim(),
            campaign_type: (formData.get("campaign_type") || "").trim(),
            flujo_proceso_id: (formData.get("flujo_proceso_id") || "").trim(),
            description: (formData.get("description") || "").trim(),
            start_date: (formData.get("start_date") || "").trim(),
            user_name: (formData.get("user_name") || "").trim(),
            bigquery_query: (formData.get("bigquery_query") || "").trim(),
            wolkvox_add_record_endpoint: (formData.get("wolkvox_add_record_endpoint") || "").trim(),
            wolkvox_delete_records_endpoint: (formData.get("wolkvox_delete_records_endpoint") || "").trim(),
            wolkvox_campaign_id: (formData.get("wolkvox_campaign_id") || "").trim(),
            server_name: (formData.get("server_name") || "").trim(),
            field_mapping: (formData.get("field_mapping") || "").trim(),
            schedule_type: type,
            schedule_value: scheduleValueForSubmit(type, formData.get("schedule_value")),
            status: formData.get("status") === "on"
        };

        try {
            JSON.parse(payload.field_mapping);
        } catch (err) {
            message("El mapeo de campos debe ser JSON válido.", false);
            return;
        }

        if (tipoRequiereFlujo(payload.campaign_type) && !payload.flujo_proceso_id) {
            message("Seleccione un flujo de proceso para campañas Llamada o WhatsApp.", false);
            return;
        }

        if (payload.schedule_type === "recurring" && payload.schedule_value) {
            try {
                JSON.parse(payload.schedule_value);
            } catch (err) {
                message("La programación recurrente debe ser JSON válido o un número de horas.", false);
                return;
            }
        }

        const id = form.dataset.id;
        const count = await validateQuery(payload.bigquery_query, id);
        if (count === null) {
            return;
        }

        const response = await fetch(id ? `/auto-campaigns/${id}` : "/auto-campaigns", {
            method: id ? "PUT" : "POST",
            headers: { "Content-Type": "application/json", "Accept": "application/json" },
            body: JSON.stringify(payload)
        });
        const data = await parseResponse(response);
        message(data.message || "Campaña guardada.", response.ok && data.success !== false);
        if (response.ok && data.success !== false) {
            const newId = id || (data.campaign && data.campaign.id);
            setTimeout(() => {
                window.location.href = newId ? `/auto-campaigns/${newId}` : "/auto-campaigns";
            }, 700);
        }
    }

    async function validateQuery(query, campaignId) {
        const resultEl = document.getElementById("precountResult");
        if (!query) {
            message("La consulta SQL es obligatoria.", false);
            return null;
        }
        if (resultEl) {
            resultEl.textContent = "Validando consulta...";
            resultEl.className = "ml-2 text-muted";
        }
        let fieldMapping = null;
        const form = document.getElementById("autoCampaignForm");
        const formData = form ? new FormData(form) : new FormData();
        const fieldMappingText = (formData.get("field_mapping") || "").trim();
        if (fieldMappingText) {
            try {
                fieldMapping = JSON.parse(fieldMappingText);
            } catch (err) {
                message("El mapeo de campos debe ser JSON válido.", false);
                return null;
            }
        }

        const response = await fetch("/auto-campaigns/test-count", {
            method: "POST",
            headers: { "Content-Type": "application/json", "Accept": "application/json" },
            body: JSON.stringify({ query: query, campaign_id: campaignId || null, field_mapping: fieldMapping })
        });
        const data = await parseResponse(response);
        if (!response.ok || data.success === false) {
            const text = data.message || "La consulta no pudo validarse.";
            if (resultEl) {
                resultEl.textContent = text;
                resultEl.className = "ml-2 text-danger";
            }
            message(text, false);
            return null;
        }

        const total = Number(data.total || 0);
        if (data.warning) {
            const text = data.warning;
            if (resultEl) {
                resultEl.textContent = text + ` Registros: ${total}`;
                resultEl.className = "ml-2 text-warning";
            }
            message(text, false);
        } else {
            if (resultEl) {
                resultEl.textContent = `Consulta válida. Registros: ${total}`;
                resultEl.className = "ml-2 text-success";
            }
        }
        return total;
    }

    document.addEventListener("click", function (event) {
        const button = event.target.closest("[data-action]");
        if (!button) return;
        postAction(button.dataset.id, button.dataset.action).catch((err) => message(err.message, false));
    });

    const form = document.getElementById("autoCampaignForm");
    if (form) {
        form.addEventListener("submit", function (event) {
            event.preventDefault();
            saveForm(form).catch((err) => message(err.message, false));
        });

        const serverSelector = form.querySelector("[name='server_name']");
        const campaignTypeSelector = form.querySelector("[name='campaign_type']");
        if (serverSelector) {
            serverSelector.addEventListener("change", toggleFlujoProcesoField);
        }
        if (campaignTypeSelector) {
            campaignTypeSelector.addEventListener("change", toggleFlujoProcesoField);
        }

        toggleFlujoProcesoField();
    }

    const precountButton = document.getElementById("precountButton");
    if (precountButton && form) {
        precountButton.addEventListener("click", function () {
            const formData = new FormData(form);
            validateQuery((formData.get("bigquery_query") || "").trim(), form.dataset.id)
                .catch((err) => message(err.message, false));
        });
    }
})();
