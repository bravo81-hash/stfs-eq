document.addEventListener('DOMContentLoaded', () => {
    // ── Tab Switching ──
    const tabs = document.querySelectorAll('.nav-links li');
    const contents = document.querySelectorAll('.tab-content');
    const pageTitle = document.getElementById('page-title');

    tabs.forEach(tab => {
        tab.addEventListener('click', () => {
            tabs.forEach(t => t.classList.remove('active'));
            contents.forEach(c => c.classList.remove('active'));

            tab.classList.add('active');
            const target = tab.getAttribute('data-tab');
            document.getElementById(target).classList.add('active');
            
            // Update title
            pageTitle.textContent = tab.textContent.trim();

            if (target === 'portfolio') fetchPortfolio();
            if (target === 'combos')    fetchCombos();
        });
    });

    // ── TWS Connection Status ──
    const twsIndicator = document.getElementById('tws-indicator');
    const twsText = document.getElementById('tws-text');

    async function checkTwsStatus() {
        try {
            const res = await fetch('/api/status');
            const data = await res.json();
            if (data.connected) {
                twsIndicator.className = 'indicator green';
                twsText.textContent = 'TWS Connected';
            } else {
                twsIndicator.className = 'indicator red';
                twsText.textContent = 'TWS Offline';
            }
        } catch (e) {
            twsIndicator.className = 'indicator red';
            twsText.textContent = 'Server Offline';
        }
    }

    // Check status on load and every 10s
    checkTwsStatus();
    setInterval(checkTwsStatus, 10000);

    // ── Launchpad ──
    const btnGenerate = document.getElementById('btn-generate');
    const regimeSelect = document.getElementById('regime-select');
    const iframe = document.getElementById('battle-card-frame');
    const placeholder = document.getElementById('card-placeholder');
    const btnText = btnGenerate.querySelector('.btn-text');
    const loader = btnGenerate.querySelector('.loader');

    btnGenerate.addEventListener('click', async () => {
        const regime = regimeSelect.value;
        
        // UI Loading State
        btnGenerate.disabled = true;
        btnText.textContent = 'Generating...';
        loader.classList.remove('hidden');
        
        try {
            const res = await fetch('/api/generate', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ regime })
            });
            const data = await res.json();
            
            if (data.ok) {
                // Load the HTML file into the iframe
                iframe.src = '/output/' + data.output_file + '?t=' + new Date().getTime();
                iframe.style.display = 'block';
                placeholder.style.display = 'none';
            } else {
                alert('Generation failed: ' + (data.error || 'Unknown error'));
            }
        } catch (e) {
            alert('Error calling server: ' + e);
        } finally {
            // Restore UI
            btnGenerate.disabled = false;
            btnText.textContent = 'Generate Battle Card';
            loader.classList.add('hidden');
        }
    });

    // ── Portfolio ──
    const btnRefreshPortfolio = document.getElementById('btn-refresh-portfolio');
    const portfolioBody = document.getElementById('portfolio-body');

    async function fetchPortfolio() {
        portfolioBody.innerHTML = '<tr class="empty-row"><td colspan="7">Loading portfolio data...</td></tr>';
        
        try {
            const res = await fetch('/api/portfolio');
            const data = await res.json();
            
            if (!data.ok) {
                portfolioBody.innerHTML = `<tr class="empty-row"><td colspan="7">Error: ${data.error}</td></tr>`;
                return;
            }

            if (data.positions.length === 0) {
                portfolioBody.innerHTML = '<tr class="empty-row"><td colspan="7">No STFS-EQ options positions found.</td></tr>';
                return;
            }

            let html = '';
            data.positions.forEach(p => {
                let badgeClass = 'badge-hold';
                if (p.signal_state === 'CLOSE_WARN') badgeClass = 'badge-warn';
                if (p.signal_state === 'CLOSE_DANGER') badgeClass = 'badge-danger';
                
                html += `
                    <tr>
                        <td><strong>${p.ticker}</strong></td>
                        <td>${p.account}</td>
                        <td>${p.structure.replace('_', ' ').replace(/\b\w/g, l => l.toUpperCase())}</td>
                        <td>${p.mark ? '$'+p.mark : 'STALE'}</td>
                        <td>${p.pnl_str}</td>
                        <td>${p.dte}</td>
                        <td><span class="badge ${badgeClass}">${p.signal_text}</span></td>
                    </tr>
                `;
            });
            portfolioBody.innerHTML = html;
        } catch (e) {
            portfolioBody.innerHTML = `<tr class="empty-row"><td colspan="7">Failed to fetch data: ${e}</td></tr>`;
        }
    }

    btnRefreshPortfolio.addEventListener('click', fetchPortfolio);

    // ── Trailing Stop Daemon Control ──
    const daemonToggle = document.getElementById('daemon-toggle');
    const daemonIndicator = document.getElementById('daemon-indicator');
    const daemonText = document.getElementById('daemon-text');

    async function checkDaemonStatus() {
        try {
            const res = await fetch('/api/daemon/status');
            const data = await res.json();
            updateDaemonUI(data.active);
            daemonToggle.disabled = false;
        } catch (e) {
            daemonText.textContent = 'Server unreachable';
            daemonToggle.disabled = true;
        }
    }

    function updateDaemonUI(isActive) {
        daemonToggle.checked = isActive;
        if (isActive) {
            daemonIndicator.className = 'status-indicator active';
            daemonText.textContent = 'Daemon Active';
        } else {
            daemonIndicator.className = 'status-indicator inactive';
            daemonText.textContent = 'Daemon Offline';
        }
    }

    daemonToggle.addEventListener('change', async (e) => {
        const action = e.target.checked ? 'start' : 'stop';
        daemonToggle.disabled = true;
        daemonText.textContent = action === 'start' ? 'Starting...' : 'Stopping...';
        
        try {
            const res = await fetch('/api/daemon/toggle', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ action })
            });
            const data = await res.json();
            if (data.ok) {
                updateDaemonUI(data.active);
            } else {
                alert('Failed to toggle daemon: ' + data.error);
                updateDaemonUI(!e.target.checked); // revert UI
            }
        } catch (err) {
            alert('Request failed');
            updateDaemonUI(!e.target.checked); // revert UI
        } finally {
            daemonToggle.disabled = false;
        }
    });

    // Initial checks
    checkDaemonStatus();
    setInterval(checkDaemonStatus, 5000); // Check daemon status every 5 seconds

    // ── Tools Tab ──
    const btnRunBacktest = document.getElementById('btn-run-backtest');
    const btnLogOutcome = document.getElementById('btn-log-outcome');
    const btnAnalyzeJournal = document.getElementById('btn-analyze-journal');
    const toolsTerminal = document.getElementById('tools-terminal');

    async function runScript(scriptName, args) {
        if(toolsTerminal) toolsTerminal.textContent = `Running ${scriptName}...\n\n`;
        try {
            const res = await fetch('/api/run_script', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ script: scriptName, args: args })
            });
            const data = await res.json();
            if (data.ok) {
                if(toolsTerminal) toolsTerminal.textContent += data.stdout + (data.stderr ? '\n' + data.stderr : '');
            } else {
                if(toolsTerminal) toolsTerminal.textContent += `Error: ${data.error}`;
            }
        } catch (e) {
            if(toolsTerminal) toolsTerminal.textContent += `Failed to fetch: ${e}`;
        }
    }

    if (btnRunBacktest) {
        btnRunBacktest.addEventListener('click', () => {
            const tickers = document.getElementById('bt-tickers').value.trim().split(' ').filter(t => t);
            if (tickers.length === 0) return alert("Enter a ticker!");
            const days = document.getElementById('bt-days').value || "1500";
            runScript('backtest.py', [...tickers, '--days', days]);
        });
    }

    if (btnLogOutcome) {
        btnLogOutcome.addEventListener('click', () => {
            const ticker = document.getElementById('log-ticker').value.trim();
            const date = document.getElementById('log-date').value.trim();
            const price = document.getElementById('log-price').value.trim();
            if (!ticker || !date || !price) return alert("Fill all log outcome fields");
            runScript('log_outcome.py', [ticker, date, price]);
        });
    }

    if (btnAnalyzeJournal) {
        btnAnalyzeJournal.addEventListener('click', () => {
            runScript('analyze_journal.py', []);
        });
    }

    // ── Settings Tab (Accounts) ──
    const accountsContainer = document.getElementById('accounts-container');
    const btnSaveAccounts = document.getElementById('btn-save-accounts');

    async function loadAccounts() {
        try {
            const res = await fetch('/api/accounts');
            const data = await res.json();
            if (data.ok) {
                renderAccounts(data.accounts);
            }
        } catch (e) {
            if(accountsContainer) accountsContainer.innerHTML = `<div style="color:red">Failed to load accounts: ${e}</div>`;
        }
    }

    function renderAccounts(accounts) {
        if(!accountsContainer) return;
        accountsContainer.innerHTML = '';
        accounts.forEach((acc) => {
            const div = document.createElement('div');
            div.style.display = 'flex';
            div.style.gap = '16px';
            div.style.alignItems = 'center';
            div.style.background = 'rgba(0,0,0,0.2)';
            div.style.padding = '12px 16px';
            div.style.borderRadius = '8px';
            div.style.border = '1px solid var(--border-color)';
            
            div.innerHTML = `
                <div style="width: 100px; font-weight: bold;">${acc.name}</div>
                <input type="hidden" class="acc-name" value="${acc.name}">
                
                <div style="display:flex; flex-direction:column; gap:4px;">
                    <label style="font-size:11px; color:var(--text-muted); text-transform:uppercase;">Equity $</label>
                    <input type="number" class="acc-eq" value="${acc.equity}" style="background: rgba(0,0,0,0.3); border: 1px solid var(--border-color); color: white; padding: 6px 10px; border-radius: 4px; width: 100px;">
                </div>
                <div style="display:flex; flex-direction:column; gap:4px;">
                    <label style="font-size:11px; color:var(--text-muted); text-transform:uppercase;">Risk %</label>
                    <input type="number" step="0.1" class="acc-rp" value="${acc.risk_pct}" style="background: rgba(0,0,0,0.3); border: 1px solid var(--border-color); color: white; padding: 6px 10px; border-radius: 4px; width: 70px;">
                </div>
                <div style="display:flex; flex-direction:column; gap:4px;">
                    <label style="font-size:11px; color:var(--text-muted); text-transform:uppercase;">Max Notional %</label>
                    <input type="number" step="0.1" class="acc-mn" value="${acc.max_notional_pct}" style="background: rgba(0,0,0,0.3); border: 1px solid var(--border-color); color: white; padding: 6px 10px; border-radius: 4px; width: 70px;">
                </div>
            `;
            accountsContainer.appendChild(div);
        });
    }

    if (btnSaveAccounts) {
        btnSaveAccounts.addEventListener('click', async () => {
            if(!accountsContainer) return;
            const rows = accountsContainer.children;
            const accounts = [];
            for (let i = 0; i < rows.length; i++) {
                const row = rows[i];
                if (!row.querySelector('.acc-name')) continue;
                accounts.push({
                    name: row.querySelector('.acc-name').value,
                    equity: parseFloat(row.querySelector('.acc-eq').value),
                    risk_pct: parseFloat(row.querySelector('.acc-rp').value),
                    max_notional_pct: parseFloat(row.querySelector('.acc-mn').value)
                });
            }

            btnSaveAccounts.disabled = true;
            btnSaveAccounts.textContent = 'Saving...';

            try {
                const res = await fetch('/api/accounts', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ accounts })
                });
                const data = await res.json();
                if (data.ok) {
                    btnSaveAccounts.textContent = 'Saved \u2713';
                    btnSaveAccounts.style.background = 'var(--success)';
                    setTimeout(() => {
                        btnSaveAccounts.textContent = 'Save Accounts';
                        btnSaveAccounts.style.background = 'var(--accent)';
                        btnSaveAccounts.disabled = false;
                    }, 2000);
                } else {
                    alert('Save failed: ' + data.error);
                    btnSaveAccounts.disabled = false;
                    btnSaveAccounts.textContent = 'Save Accounts';
                }
            } catch (e) {
                alert('Request failed');
                btnSaveAccounts.disabled = false;
                btnSaveAccounts.textContent = 'Save Accounts';
            }
        });
    }

    // ── Manual Combos Tab ──
    const btnRefreshCombos = document.getElementById('btn-refresh-combos');
    const combosContainer  = document.getElementById('combos-container');

    function _fmtGreek(v, signed = true) {
        if (v === null || v === undefined) return '<span style="color:var(--text-muted)">?</span>';
        const s = signed ? (v >= 0 ? '+' : '') : '';
        return s + v;
    }

    function _fmtPnl(v) {
        if (v === null || v === undefined) return '<span style="color:var(--text-muted)">?</span>';
        const color = v >= 0 ? 'var(--success)' : 'var(--danger)';
        return `<span style="color:${color};font-weight:600">${v >= 0 ? '+' : ''}$${Math.abs(v).toLocaleString()}</span>`;
    }

    async function fetchCombos() {
        if (!combosContainer) return;
        combosContainer.innerHTML = '<div class="glass-panel" style="padding:40px;text-align:center;color:var(--text-muted)">Loading combo data from TWS…</div>';

        try {
            const res  = await fetch('/api/manual_portfolio');
            const data = await res.json();

            if (!data.ok) {
                combosContainer.innerHTML = `<div class="glass-panel" style="padding:40px;text-align:center;color:var(--danger)">Error: ${data.error}</div>`;
                return;
            }
            if (data.combos.length === 0) {
                combosContainer.innerHTML = '<div class="glass-panel" style="padding:40px;text-align:center;color:var(--text-muted)">No combos defined in manual_combos.yaml.</div>';
                return;
            }

            combosContainer.innerHTML = data.combos.map(combo => {
                const dteBadge = combo.dte >= 0
                    ? `<span class="badge badge-hold" style="margin-left:8px">DTE ${combo.dte}</span>`
                    : '';
                const partialNote = (combo.partial || combo.has_error)
                    ? '<span style="font-size:11px;color:var(--warning);margin-left:8px">⚠ partial data</span>' : '';

                const legRows = combo.legs.map(leg => {
                    const errCell = leg.error
                        ? `<td colspan="5" style="color:var(--warning);font-size:11px;padding-left:8px">⚠ ${leg.error}</td>`
                        : `<td style="text-align:right;font-size:12px">${_fmtGreek(leg.delta)}</td>
                           <td style="text-align:right;font-size:12px">${_fmtGreek(leg.gamma, false)}</td>
                           <td style="text-align:right;font-size:12px">${_fmtGreek(leg.theta)}</td>
                           <td style="text-align:right;font-size:12px">${_fmtGreek(leg.vega)}</td>`;
                    return `<tr>
                        <td style="font-family:monospace;font-size:13px">${leg.label}</td>
                        <td style="text-align:center">${leg.qty >= 0 ? '+' : ''}${leg.qty}</td>
                        <td style="text-align:right">$${leg.fill.toFixed(2)}</td>
                        <td style="text-align:right">${leg.mark !== null ? '$'+leg.mark.toFixed(2) : '<span style="color:var(--text-muted)">—</span>'}</td>
                        <td style="text-align:right">${_fmtPnl(leg.pnl)}</td>
                        ${errCell}
                    </tr>`;
                }).join('');

                const t = combo.total;
                return `
                <div class="glass-panel" style="padding:20px;margin-bottom:16px">
                    <div style="display:flex;align-items:center;margin-bottom:14px;gap:8px">
                        <h3 style="font-size:16px;font-weight:600;margin:0">${combo.name}</h3>
                        ${dteBadge}
                        <span style="flex:1"></span>
                        ${partialNote}
                        <span style="font-size:20px;font-weight:700">${_fmtPnl(t.pnl)}</span>
                    </div>
                    <div class="table-container" style="padding:0;background:transparent;border:none">
                        <table style="width:100%">
                            <thead>
                                <tr>
                                    <th>Leg</th><th style="text-align:center">Qty</th>
                                    <th style="text-align:right">Fill</th><th style="text-align:right">Mark</th>
                                    <th style="text-align:right">P&L</th><th style="text-align:right">Delta</th>
                                    <th style="text-align:right">Gamma</th><th style="text-align:right">Theta</th>
                                    <th style="text-align:right">Vega</th>
                                </tr>
                            </thead>
                            <tbody>${legRows}</tbody>
                            <tfoot>
                                <tr style="border-top:1px solid var(--border-color);font-weight:600">
                                    <td colspan="4" style="color:var(--text-muted);font-size:12px;padding-top:8px">TOTAL</td>
                                    <td style="text-align:right;padding-top:8px">${_fmtPnl(t.pnl)}</td>
                                    <td style="text-align:right;font-size:12px;padding-top:8px">${_fmtGreek(t.delta)}</td>
                                    <td style="text-align:right;font-size:12px;padding-top:8px">${_fmtGreek(t.gamma, false)}</td>
                                    <td style="text-align:right;font-size:12px;padding-top:8px">${_fmtGreek(t.theta)}</td>
                                    <td style="text-align:right;font-size:12px;padding-top:8px">${_fmtGreek(t.vega)}</td>
                                </tr>
                            </tfoot>
                        </table>
                    </div>
                </div>`;
            }).join('');

        } catch (e) {
            combosContainer.innerHTML = `<div class="glass-panel" style="padding:40px;text-align:center;color:var(--danger)">Failed to fetch: ${e}</div>`;
        }
    }

    if (btnRefreshCombos) btnRefreshCombos.addEventListener('click', fetchCombos);

    // Load accounts initially
    loadAccounts();
});
