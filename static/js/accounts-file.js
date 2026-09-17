/* Accounts / Year end — FRS 105 micro-entity ledger inside the portal chrome. */
(function () {
    const SCREENS = [
        { id: 'home', group: 'Accounting', label: 'Home' },
        { id: 'coa', group: 'Accounting', label: 'Chart of accounts' },
        { id: 'journals', group: 'Accounting', label: 'Manual journals' },
        { id: 'organisation', group: 'Accounting', label: 'Organisation' },
        { id: 'pl', group: 'Reporting', label: 'Profit and loss' },
        { id: 'bs', group: 'Reporting', label: 'Balance sheet' },
        { id: 'tb', group: 'Reporting', label: 'Trial balance' },
        { id: 'year-end', group: 'Year end', label: 'Annual accounts' },
    ];

    const state = {
        scope: 'client',
        screen: 'home',
        companies: [],
        companyId: 0,
        workspace: null,
        loading: false,
        error: '',
        journalLines: [
            { code: '', debit: '', credit: '', description: '' },
            { code: '', debit: '', credit: '', description: '' },
        ],
    };

    function rootEl() {
        return document.getElementById(state.scope === 'admin' ? 'admin-accounts-root' : 'client-accounts-root');
    }

    function apiBase() {
        return state.scope === 'admin' ? '/api/admin/accounts-file' : '/api/client/accounts-file';
    }

    function storageKey() {
        const uid = (window.currentUser && currentUser.id) || 'anon';
        return `brixen_accounts_company_${state.scope}_${uid}`;
    }

    function gbp(value) {
        const n = Number(value || 0);
        const abs = Math.abs(n).toLocaleString('en-GB', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
        return `${n < 0 ? '−' : ''}£${abs}`;
    }

    function ukDate(value) {
        if (typeof formatUkNumericDate === 'function') {
            const formatted = formatUkNumericDate(value);
            if (formatted) return formatted;
        }
        if (!value) return '—';
        const d = new Date(`${String(value).slice(0, 10)}T00:00:00`);
        if (Number.isNaN(d.getTime())) return String(value);
        return d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' });
    }

    function h(value) {
        return typeof escapeHtml === 'function' ? escapeHtml(value == null ? '' : value) : String(value == null ? '' : value)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }

    function typeLabel(type) {
        return ({
            BANK: 'Bank',
            CURRENT_ASSET: 'Current asset',
            FIXED_ASSET: 'Fixed asset',
            FIXED_ASSET_CONTRA: 'Depreciation',
            CURRENT_LIABILITY: 'Current liability',
            LONG_TERM_LIABILITY: 'Long-term liability',
            PROVISION: 'Provision',
            EQUITY: 'Capital',
            INCOME: 'Income',
            COS: 'Cost of sales',
            EXPENSE: 'Expense',
        })[type] || type;
    }

    async function readJson(res) {
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data.status === 'error') {
            throw new Error(data.message || `Unable to load accounts (${res.status}).`);
        }
        return data;
    }

    async function loadCompanyList() {
        const res = await fetch(apiBase(), { credentials: 'same-origin' });
        const data = await readJson(res);
        state.companies = data.companies || [];
        const saved = Number(localStorage.getItem(storageKey()) || 0);
        const stillThere = state.companies.some((row) => Number(row.id) === saved);
        if (stillThere) state.companyId = saved;
        else if (state.companies.length) state.companyId = Number(state.companies[0].id);
        else state.companyId = 0;
    }

    async function loadWorkspace() {
        if (!state.companyId) {
            state.workspace = null;
            return;
        }
        const res = await fetch(`${apiBase()}/${state.companyId}`, { credentials: 'same-origin' });
        state.workspace = await readJson(res);
    }

    async function postAction(action, payload, method) {
        const res = await fetch(`${apiBase()}/${state.companyId}/${action}`, {
            method: method || 'POST',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload || {}),
        });
        state.workspace = await readJson(res);
    }

    function setScreen(screen) {
        state.screen = screen || 'home';
        const viewName = state.scope === 'admin'
            ? (screen === 'year-end' ? 'admin-year-end' : 'admin-accounts')
            : (screen === 'year-end' ? 'client-year-end' : 'client-accounts');
        if (typeof syncViewHash === 'function') syncViewHash(viewName);
        document.querySelectorAll('.nav-item, .top-menu-item').forEach((item) => {
            const view = item.getAttribute('data-view');
            item.classList.toggle('active', view === viewName);
        });
        render();
    }

    function headerMeta() {
        const org = state.workspace && state.workspace.organisation;
        if (!org) return 'Select a company, then load sample books or start a blank chart of accounts.';
        const bits = [org.registered_name];
        if (org.company_number) bits.push(`No. ${org.company_number}`);
        if (org.period_end) bits.push(`Year end ${ukDate(org.period_end)}`);
        return bits.join(' · ');
    }

    function renderShell(inner) {
        const companies = state.companies.map((row) => (
            `<option value="${Number(row.id)}" ${Number(row.id) === Number(state.companyId) ? 'selected' : ''}>${h(row.ledger_name || row.name)}${row.company_number ? ` (${h(row.company_number)})` : ''}</option>`
        )).join('');
        const groups = ['Accounting', 'Reporting', 'Year end'];
        const tabs = groups.map((group) => {
            const buttons = SCREENS.filter((s) => s.group === group).map((s) => (
                `<button type="button" class="${state.screen === s.id ? 'is-active' : ''}" data-accounts-screen="${s.id}">${h(s.label)}</button>`
            )).join('');
            return `<div class="accounts-file-nav-group"><span>${h(group)}</span><div class="portfolio-filter-bar accounts-file-tabs" role="tablist" aria-label="${h(group)}">${buttons}</div></div>`;
        }).join('');
        return `
            <div class="page-title-row accounts-file-head">
                <div class="page-title-text">
                    <h1>${state.screen === 'year-end' ? 'Year end' : 'Accounts'}</h1>
                    <p>${h(headerMeta())}</p>
                </div>
                <div class="accounts-file-toolbar">
                    <label class="accounts-file-company">
                        <span>Company</span>
                        <select id="accounts-file-company" class="select-filter" ${state.companies.length ? '' : 'disabled'}>
                            ${companies || '<option value="">No registered companies</option>'}
                        </select>
                    </label>
                </div>
            </div>
            <nav class="accounts-file-nav" aria-label="Accounts workspace">${tabs}</nav>
            ${state.error ? `<div class="dash-banner-error" role="alert">${h(state.error)}</div>` : ''}
            ${inner}
        `;
    }

    function emptyState() {
        if (!state.companies.length) {
            return `
                <div class="accounts-file-empty">
                    <h2>No company to keep books for</h2>
                    <p>Register or add a UK company in Company Registered first. Accounts and year-end packs attach to that company, not a separate product.</p>
                </div>`;
        }
        return `
            <div class="accounts-file-empty">
                <h2>No accounts file yet</h2>
                <p>Open a UK chart of accounts for this company, post year-end journals, then prepare FRS 105 micro-entity accounts. Load the Willow &amp; Thorn sample to see a complete year in the portal.</p>
                <div class="accounts-file-empty-actions">
                    <button type="button" class="btn-primary" data-accounts-action="sample">Load sample organisation</button>
                    <button type="button" class="btn-secondary" data-accounts-action="start">Start a blank chart of accounts</button>
                </div>
            </div>`;
    }

    function loadingState() {
        return `<div class="accounts-file-empty"><p>Loading the accounts file…</p></div>`;
    }

    function homeScreen(ws) {
        const home = ws.home || {};
        const banks = (home.bank_accounts || []).map((row) => (
            `<li><span>${h(row.name)}</span><strong>${gbp(row.balance)}</strong></li>`
        )).join('') || '<li>No bank balances yet.</li>';
        const watch = (home.watchlist || []).map((row) => (
            `<tr><td>${h(row.code)}</td><td>${h(row.name)}</td><td class="num">${gbp(row.ytd)}</td><td class="num">${gbp(row.prior)}</td></tr>`
        )).join('') || '<tr><td colspan="4">Star accounts on the chart of accounts to pin them here.</td></tr>';
        const items = (home.watch_items || []).map((item) => (
            `<article class="accounts-watch-item is-${h(item.tone)}"><h3>${h(item.title)}</h3><p>${h(item.detail)}</p></article>`
        )).join('');
        return `
            <div class="stats-grid accounts-file-stats">
                <div class="stat-card"><div class="stat-info"><div class="label">Bank</div><div class="value">${gbp(home.bank_total)}</div></div></div>
                <div class="stat-card"><div class="stat-info"><div class="label">Money in</div><div class="value">${gbp(home.money_in)}</div></div></div>
                <div class="stat-card"><div class="stat-info"><div class="label">Money out</div><div class="value">${gbp(home.money_out)}</div></div></div>
                <div class="stat-card"><div class="stat-info"><div class="label">Profit for the year</div><div class="value">${gbp((ws.profit_and_loss || {}).profit)}</div></div></div>
            </div>
            <div class="accounts-file-grid">
                <section class="accounts-file-card">
                    <h2>Bank balances</h2>
                    <ul class="accounts-bank-list">${banks}</ul>
                </section>
                <section class="accounts-file-card">
                    <h2>Watch items</h2>
                    <div class="accounts-watch-list">${items}</div>
                </section>
            </div>
            <section class="accounts-file-card">
                <h2>Watchlist</h2>
                <div class="data-table-container">
                    <table class="data-table">
                        <thead><tr><th>Code</th><th>Account</th><th>This year</th><th>Last year</th></tr></thead>
                        <tbody>${watch}</tbody>
                    </table>
                </div>
            </section>`;
    }

    function displayBalance(acc) {
        if (['INCOME', 'EQUITY', 'CURRENT_LIABILITY', 'LONG_TERM_LIABILITY', 'PROVISION', 'FIXED_ASSET_CONTRA'].includes(acc.account_type)) {
            return gbp(Number(acc.ytd_credit || 0) - Number(acc.ytd_debit || 0));
        }
        return gbp(acc.ytd_net);
    }

    function coaScreen(ws) {
        const rows = (ws.accounts || []).map((acc) => `
            <tr>
                <td>${h(acc.code)}</td>
                <td>${h(acc.name)}</td>
                <td>${h(typeLabel(acc.account_type))}</td>
                <td class="num">${displayBalance(acc)}</td>
                <td class="num">${gbp(acc.prior_net)}</td>
                <td><button type="button" class="accounts-star ${acc.watched ? 'is-on' : ''}" data-accounts-watch="${h(acc.code)}" aria-label="${acc.watched ? 'Remove from watchlist' : 'Add to watchlist'}">${acc.watched ? '★' : '☆'}</button></td>
            </tr>
        `).join('');
        return `
            <section class="accounts-file-card">
                <h2>UK chart of accounts</h2>
                <p class="accounts-file-help">Nominal codes, type, year-to-date and last year. Figures here plus journals feed the reports and the FRS 105 pack.</p>
                <div class="data-table-container">
                    <table class="data-table">
                        <thead><tr><th>Code</th><th>Account</th><th>Type</th><th>YTD</th><th>Last year</th><th></th></tr></thead>
                        <tbody>${rows || '<tr><td colspan="6">No nominal codes yet.</td></tr>'}</tbody>
                    </table>
                </div>
            </section>`;
    }

    function journalsScreen(ws) {
        const options = (ws.accounts || []).map((acc) => `<option value="${h(acc.code)}">${h(acc.code)} ${h(acc.name)}</option>`).join('');
        const lineRows = state.journalLines.map((line, index) => `
            <tr>
                <td>
                    <select data-jline="${index}" data-jfield="code" class="select-filter">
                        <option value="">Nominal</option>
                        ${options.replace(/value="([^"]+)"/g, (m, code) => `value="${code}"${code === line.code ? ' selected' : ''}`)}
                    </select>
                </td>
                <td><input data-jline="${index}" data-jfield="description" class="select-filter" value="${h(line.description)}" placeholder="Narration"></td>
                <td><input data-jline="${index}" data-jfield="debit" class="select-filter num-input" inputmode="decimal" value="${h(line.debit)}" placeholder="0.00"></td>
                <td><input data-jline="${index}" data-jfield="credit" class="select-filter num-input" inputmode="decimal" value="${h(line.credit)}" placeholder="0.00"></td>
            </tr>
        `).join('');
        const posted = (ws.journals || []).map((jnl) => {
            const lines = (jnl.lines || []).map((line) => (
                `<tr><td>${h(line.code)}</td><td>${h(line.name)}</td><td>${h(line.description)}</td><td class="num">${line.debit ? gbp(line.debit) : ''}</td><td class="num">${line.credit ? gbp(line.credit) : ''}</td></tr>`
            )).join('');
            return `<article class="accounts-journal">
                <header><strong>${h(jnl.reference || 'Journal')}</strong> · ${h(ukDate(jnl.journal_date))} · ${h(jnl.narration)}</header>
                <table class="data-table"><thead><tr><th>Code</th><th>Account</th><th>Description</th><th>Debit</th><th>Credit</th></tr></thead><tbody>${lines}</tbody></table>
            </article>`;
        }).join('') || '<p class="accounts-file-help">No journals posted yet. Year-end adjustments (depreciation, accruals, tax) belong here.</p>';
        return `
            <section class="accounts-file-card">
                <h2>Post a manual journal</h2>
                <p class="accounts-file-help">Debits must equal credits. Use this for year-end adjustments, not day-to-day invoicing.</p>
                <div class="accounts-journal-form">
                    <label>Date <input id="accounts-jnl-date" type="date" class="select-filter" value="${h((ws.organisation || {}).period_end || '')}"></label>
                    <label>Reference <input id="accounts-jnl-ref" class="select-filter" placeholder="YE-18"></label>
                    <label class="accounts-span">Narration <input id="accounts-jnl-narration" class="select-filter" placeholder="Year-end depreciation"></label>
                </div>
                <div class="data-table-container">
                    <table class="data-table">
                        <thead><tr><th>Account</th><th>Description</th><th>Debit</th><th>Credit</th></tr></thead>
                        <tbody>${lineRows}</tbody>
                    </table>
                </div>
                <div class="accounts-file-empty-actions">
                    <button type="button" class="btn-secondary" data-accounts-action="add-line">Add line</button>
                    <button type="button" class="btn-primary" data-accounts-action="post-journal">Post journal</button>
                </div>
            </section>
            <section class="accounts-file-card">
                <h2>Posted journals</h2>
                ${posted}
            </section>`;
    }

    function organisationScreen(ws) {
        const org = ws.organisation || {};
        const directors = (org.directors && org.directors.length ? org.directors : [{ name: '', role: 'Director' }]);
        const directorFields = directors.map((d, i) => `
            <div class="accounts-director-row">
                <input class="select-filter" data-director="${i}" data-dfield="name" value="${h(d.name)}" placeholder="Director name">
                <input class="select-filter" data-director="${i}" data-dfield="role" value="${h(d.role || 'Director')}" placeholder="Role">
            </div>
        `).join('');
        const exclusions = (ws.year_end && ws.year_end.exclusions) || [];
        const exclusionBoxes = exclusions.map((item) => `
            <label class="accounts-check"><input type="checkbox" data-exclusion="${h(item.key)}" ${item.flagged ? 'checked' : ''}> ${h(item.label)}</label>
        `).join('');
        const notes = org.notes || {};
        return `
            <section class="accounts-file-card">
                <h2>Organisation</h2>
                <p class="accounts-file-help">Registered details used on the statutory pack. This is ledger settings, not a filing wizard.</p>
                <div class="accounts-org-grid">
                    <label>Registered name <input id="acc-org-name" class="select-filter" value="${h(org.registered_name)}"></label>
                    <label>Company number <input id="acc-org-number" class="select-filter" value="${h(org.company_number)}" maxlength="8"></label>
                    <label class="accounts-span">Registered office <input id="acc-org-office" class="select-filter" value="${h(org.registered_office)}"></label>
                    <label>Incorporated <input id="acc-org-inc" type="date" class="select-filter" value="${h(org.incorporation_date || '')}"></label>
                    <label>Period start <input id="acc-org-start" type="date" class="select-filter" value="${h(org.period_start || '')}"></label>
                    <label>Period end <input id="acc-org-end" type="date" class="select-filter" value="${h(org.period_end || '')}"></label>
                    <label>Prior period start <input id="acc-org-pstart" type="date" class="select-filter" value="${h(org.prior_period_start || '')}"></label>
                    <label>Prior period end <input id="acc-org-pend" type="date" class="select-filter" value="${h(org.prior_period_end || '')}"></label>
                    <label>Average employees <input id="acc-org-emp" type="number" min="0" class="select-filter" value="${h(org.employees)}"></label>
                    <label>Prior-year employees <input id="acc-org-pemp" type="number" min="0" class="select-filter" value="${h(org.prior_employees)}"></label>
                    <label class="accounts-check accounts-span"><input id="acc-org-first" type="checkbox" ${org.is_first_accounts ? 'checked' : ''}> First accounting period (longer than 12 months uses the s442 special deadline)</label>
                </div>
                <h3>Directors</h3>
                <div id="acc-org-directors">${directorFields}</div>
                <button type="button" class="btn-secondary btn-table" data-accounts-action="add-director">Add director</button>
                <h3>Micro-entity exclusions</h3>
                <div class="accounts-exclusion-grid">${exclusionBoxes}</div>
                <h3>Notes at the foot of the balance sheet</h3>
                <label>Off-balance sheet arrangements <textarea id="acc-note-obs" class="select-filter accounts-textarea">${h(notes.off_balance_sheet)}</textarea></label>
                <label>Directors’ advances, credits and guarantees <textarea id="acc-note-adv" class="select-filter accounts-textarea">${h(notes.directors_advances)}</textarea></label>
                <label>Commitments, guarantees and contingencies <textarea id="acc-note-com" class="select-filter accounts-textarea">${h(notes.commitments)}</textarea></label>
                <div class="accounts-file-empty-actions">
                    <button type="button" class="btn-primary" data-accounts-action="save-org">Save organisation</button>
                </div>
            </section>`;
    }

    function reportScreen(title, intro, lines, extra) {
        const rows = (lines || []).map((line) => `
            <tr class="${line.is_total ? 'is-total' : ''}">
                <td>${h(line.code ? `${line.code}. ` : '')}${h(line.label || line.name)}</td>
                <td class="num">${gbp(line.current != null ? line.current : line.debit)}</td>
                <td class="num">${gbp(line.prior != null ? line.prior : line.credit)}</td>
            </tr>
        `).join('');
        return `
            <section class="accounts-file-card">
                <h2>${h(title)}</h2>
                <p class="accounts-file-help">${h(intro)}</p>
                <div class="data-table-container">
                    <table class="data-table accounts-report-table">
                        <thead><tr><th></th><th>This year</th><th>Last year</th></tr></thead>
                        <tbody>${rows}</tbody>
                    </table>
                </div>
                ${extra || ''}
            </section>`;
    }

    function tbScreen(ws) {
        const tb = ws.trial_balance || { rows: [] };
        const rows = (tb.rows || []).map((row) => `
            <tr>
                <td>${h(row.code)}</td>
                <td>${h(row.name)}</td>
                <td class="num">${row.debit ? gbp(row.debit) : ''}</td>
                <td class="num">${row.credit ? gbp(row.credit) : ''}</td>
            </tr>
        `).join('');
        return `
            <section class="accounts-file-card">
                <h2>Trial balance</h2>
                <p class="accounts-file-help">${tb.agrees ? 'The trial balance agrees.' : `The trial balance is out by ${gbp(tb.difference)}.`} Dated from the same books as the P&amp;L and balance sheet.</p>
                <div class="data-table-container">
                    <table class="data-table">
                        <thead><tr><th>Code</th><th>Account</th><th>Debit</th><th>Credit</th></tr></thead>
                        <tbody>${rows}
                            <tr class="is-total"><td></td><td>Totals</td><td class="num">${gbp(tb.total_debit)}</td><td class="num">${gbp(tb.total_credit)}</td></tr>
                        </tbody>
                    </table>
                </div>
            </section>`;
    }

    function yearEndScreen(ws) {
        const ye = ws.year_end || {};
        const org = ws.organisation || {};
        const size = ye.size || { details: [] };
        const sizeRows = (size.details || []).map((row) => `
            <tr>
                <td>${h(row.label)}</td>
                <td class="num">${row.label === 'Average employees' ? String(row.actual) : gbp(row.actual)}</td>
                <td class="num">${row.label === 'Average employees' ? `≤ ${row.limit}` : `≤ ${gbp(row.limit)}`}</td>
                <td>${row.met ? 'Met' : 'Not met'}</td>
            </tr>
        `).join('');
        const statements = (ye.statements || []).map((st) => `
            <article class="accounts-file-note"><h3>${h(st.title)}</h3><p>${h(st.body)}</p><p class="accounts-file-help">${h(st.law)}</p></article>
        `).join('');
        const notes = (ye.notes || []).map((note) => `
            <article class="accounts-file-note"><h3>${h(note.title)}</h3><p>${h(note.body)}</p><p class="accounts-file-help">${h(note.law)}</p></article>
        `).join('');
        const plLines = ((ws.profit_and_loss || {}).lines || []).map((line) => `
            <tr class="${line.is_total ? 'is-total' : ''}"><td>${h(line.code)}. ${h(line.label)}</td><td class="num">${gbp(line.current)}</td></tr>
        `).join('');
        const bsLines = ((ws.balance_sheet || {}).lines || []).map((line) => `
            <tr class="${line.is_total ? 'is-total' : ''}"><td>${h(line.code)}. ${h(line.label)}</td><td class="num">${gbp(line.current)}</td></tr>
        `).join('');
        return `
            <div class="accounts-file-banner ${ye.can_file_micro ? 'is-ok' : 'is-warn'}">
                <strong>${ye.can_file_micro ? 'This company may prepare FRS 105 micro-entity accounts.' : 'This company cannot currently file as a micro-entity.'}</strong>
                <p>${h(size.rule || '')}${ye.exclusion_labels && ye.exclusion_labels.length ? ` Exclusions: ${ye.exclusion_labels.join(', ')}.` : ''}</p>
            </div>
            <section class="accounts-file-card">
                <h2>Size test (CA 2006 s384A)</h2>
                <p class="accounts-file-help">${size.from_6_apr_2025 ? 'Period begins on or after 6 April 2025: turnover £1m, balance sheet total £500k, 10 employees. Increased thresholds are treated as if they applied in the prior year.' : 'Period begins before 6 April 2025: turnover £632k, balance sheet total £316k, 10 employees.'} Meet any two of the three.</p>
                <div class="data-table-container">
                    <table class="data-table">
                        <thead><tr><th>Condition</th><th>This year</th><th>Threshold</th><th></th></tr></thead>
                        <tbody>${sizeRows}</tbody>
                    </table>
                </div>
                <p>Filing deadline: <strong>${h(ukDate(org.filing_due))}</strong>. ${h(org.filing_due_note || '')}</p>
            </section>
            <div class="accounts-file-grid">
                <section class="accounts-file-card">
                    <h2>Micro-entity profit and loss</h2>
                    <table class="data-table"><tbody>${plLines}</tbody></table>
                </section>
                <section class="accounts-file-card">
                    <h2>Format 1 balance sheet</h2>
                    <table class="data-table"><tbody>${bsLines}</tbody></table>
                </section>
            </div>
            <section class="accounts-file-card">
                <h2>Required notes</h2>
                ${notes}
            </section>
            <section class="accounts-file-card">
                <h2>Director and audit-exemption statements</h2>
                ${statements}
                <p class="accounts-file-help">${h(ye.filing_note || '')}</p>
            </section>
            <section class="accounts-file-card">
                <h2>Export</h2>
                <p class="accounts-file-help">${h(ye.ixbrl_gap || '')}</p>
                <div class="accounts-file-empty-actions">
                    <button type="button" class="btn-primary" data-accounts-export="members">Members’ HTML pack</button>
                    <button type="button" class="btn-secondary" data-accounts-export="filleted">Companies House filleted pack</button>
                    <button type="button" class="btn-secondary" data-accounts-export="ixbrl">Draft iXBRL</button>
                </div>
            </section>`;
    }

    function innerContent() {
        if (state.loading) return loadingState();
        if (!state.companyId || !state.workspace || state.workspace.empty) return emptyState();
        const ws = state.workspace;
        const org = ws.organisation || {};
        if (state.screen === 'home') return homeScreen(ws);
        if (state.screen === 'coa') return coaScreen(ws);
        if (state.screen === 'journals') return journalsScreen(ws);
        if (state.screen === 'organisation') return organisationScreen(ws);
        if (state.screen === 'pl') {
            return reportScreen(
                'Profit and loss',
                `Management P&L for the year ended ${ukDate(org.period_end)}, mapped from the same nominals as the statutory Section C format.`,
                (ws.profit_and_loss || {}).lines,
            );
        }
        if (state.screen === 'bs') {
            const extra = (ws.balance_sheet || {}).balances
                ? '<p class="accounts-file-help">The balance sheet agrees: net assets equal capital and reserves.</p>'
                : `<p class="dash-banner-error">Balance sheet out by ${gbp((ws.balance_sheet || {}).difference)}.</p>`;
            return reportScreen(
                'Balance sheet',
                `Management balance sheet as at ${ukDate(org.period_end)}.`,
                (ws.balance_sheet || {}).lines,
                extra,
            );
        }
        if (state.screen === 'tb') return tbScreen(ws);
        if (state.screen === 'year-end') return yearEndScreen(ws);
        return homeScreen(ws);
    }

    function render() {
        const root = rootEl();
        if (!root) return;
        root.innerHTML = renderShell(innerContent());
        if (window.lucide && typeof lucide.createIcons === 'function') lucide.createIcons();
        bind(root);
    }

    function collectOrganisation() {
        const directors = [];
        document.querySelectorAll('[data-director][data-dfield="name"]').forEach((input) => {
            const i = Number(input.getAttribute('data-director'));
            const role = document.querySelector(`[data-director="${i}"][data-dfield="role"]`);
            directors.push({ name: input.value, role: role ? role.value : 'Director' });
        });
        const exclusions = {};
        document.querySelectorAll('[data-exclusion]').forEach((box) => {
            exclusions[box.getAttribute('data-exclusion')] = box.checked;
        });
        return {
            registered_name: (document.getElementById('acc-org-name') || {}).value,
            company_number: (document.getElementById('acc-org-number') || {}).value,
            registered_office: (document.getElementById('acc-org-office') || {}).value,
            incorporation_date: (document.getElementById('acc-org-inc') || {}).value,
            period_start: (document.getElementById('acc-org-start') || {}).value,
            period_end: (document.getElementById('acc-org-end') || {}).value,
            prior_period_start: (document.getElementById('acc-org-pstart') || {}).value,
            prior_period_end: (document.getElementById('acc-org-pend') || {}).value,
            employees: (document.getElementById('acc-org-emp') || {}).value,
            prior_employees: (document.getElementById('acc-org-pemp') || {}).value,
            is_first_accounts: !!(document.getElementById('acc-org-first') || {}).checked,
            directors,
            exclusions,
            notes: {
                off_balance_sheet: (document.getElementById('acc-note-obs') || {}).value,
                directors_advances: (document.getElementById('acc-note-adv') || {}).value,
                commitments: (document.getElementById('acc-note-com') || {}).value,
            },
        };
    }

    function bind(root) {
        const select = document.getElementById('accounts-file-company');
        if (select) {
            select.addEventListener('change', async () => {
                state.companyId = Number(select.value || 0);
                localStorage.setItem(storageKey(), String(state.companyId || ''));
                await refresh();
            });
        }
        root.querySelectorAll('[data-accounts-screen]').forEach((btn) => {
            btn.addEventListener('click', () => setScreen(btn.getAttribute('data-accounts-screen')));
        });
        root.querySelectorAll('[data-accounts-action]').forEach((btn) => {
            btn.addEventListener('click', () => handleAction(btn.getAttribute('data-accounts-action')));
        });
        root.querySelectorAll('[data-accounts-watch]').forEach((btn) => {
            btn.addEventListener('click', () => handleWatch(btn.getAttribute('data-accounts-watch')));
        });
        root.querySelectorAll('[data-accounts-export]').forEach((btn) => {
            btn.addEventListener('click', () => handleExport(btn.getAttribute('data-accounts-export')));
        });
        root.querySelectorAll('[data-jline]').forEach((input) => {
            input.addEventListener('input', () => {
                const i = Number(input.getAttribute('data-jline'));
                const field = input.getAttribute('data-jfield');
                if (!state.journalLines[i]) state.journalLines[i] = { code: '', debit: '', credit: '', description: '' };
                state.journalLines[i][field] = input.value;
            });
        });
    }

    async function handleAction(action) {
        state.error = '';
        try {
            if (action === 'sample') {
                await postAction('sample', {});
                state.screen = 'home';
            } else if (action === 'start') {
                await postAction('start', {});
                state.screen = 'coa';
            } else if (action === 'add-line') {
                state.journalLines.push({ code: '', debit: '', credit: '', description: '' });
                render();
                return;
            } else if (action === 'add-director') {
                const wrap = document.getElementById('acc-org-directors');
                if (wrap) {
                    const i = wrap.querySelectorAll('[data-director][data-dfield="name"]').length;
                    const row = document.createElement('div');
                    row.className = 'accounts-director-row';
                    row.innerHTML = `<input class="select-filter" data-director="${i}" data-dfield="name" placeholder="Director name"><input class="select-filter" data-director="${i}" data-dfield="role" value="Director" placeholder="Role">`;
                    wrap.appendChild(row);
                }
                return;
            } else if (action === 'save-org') {
                await postAction('organisation', collectOrganisation(), 'PUT');
            } else if (action === 'post-journal') {
                await postAction('journals', {
                    journal_date: (document.getElementById('accounts-jnl-date') || {}).value,
                    reference: (document.getElementById('accounts-jnl-ref') || {}).value,
                    narration: (document.getElementById('accounts-jnl-narration') || {}).value,
                    lines: state.journalLines,
                });
                state.journalLines = [
                    { code: '', debit: '', credit: '', description: '' },
                    { code: '', debit: '', credit: '', description: '' },
                ];
            }
        } catch (err) {
            state.error = err.message || 'Something went wrong.';
        }
        render();
    }

    async function handleWatch(code) {
        try {
            await postAction('watch', { code });
        } catch (err) {
            state.error = err.message || 'Could not update the watchlist.';
        }
        render();
    }

    function handleExport(kind) {
        if (!state.companyId) return;
        window.location.href = `${apiBase()}/${state.companyId}/export/${kind}`;
    }

    async function refresh() {
        state.loading = true;
        state.error = '';
        render();
        try {
            await loadWorkspace();
        } catch (err) {
            state.error = err.message || 'Unable to load this accounts file.';
            state.workspace = null;
        } finally {
            state.loading = false;
            render();
        }
    }

    window.loadAccountsFileView = async function loadAccountsFileView(scope, screen) {
        state.scope = scope === 'admin' ? 'admin' : 'client';
        state.screen = screen || 'home';
        state.loading = true;
        state.error = '';
        render();
        try {
            await loadCompanyList();
            await loadWorkspace();
        } catch (err) {
            state.error = err.message || 'Unable to open Accounts.';
        } finally {
            state.loading = false;
            render();
        }
    };
})();
