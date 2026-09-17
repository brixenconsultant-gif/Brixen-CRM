/* Accounts / Year end — bank statement to Companies House, inside the portal. */
(function () {
    const SCREENS = [
        { id: 'home', group: 'Your books', label: 'Home' },
        { id: 'bank', group: 'Your books', label: 'Bank statement' },
        { id: 'review', group: 'Your books', label: 'Review' },
        { id: 'reports', group: 'Your books', label: 'Reports' },
        { id: 'file', group: 'Year end', label: 'File at Companies House' },
        { id: 'organisation', group: 'More', label: 'Company details' },
        { id: 'coa', group: 'More', label: 'Chart of accounts' },
        { id: 'journals', group: 'More', label: 'Manual journals' },
        { id: 'pl', group: 'More', label: 'Profit and loss' },
        { id: 'bs', group: 'More', label: 'Balance sheet' },
        { id: 'tb', group: 'More', label: 'Trial balance' },
        { id: 'year-end', group: 'More', label: 'Statutory notes' },
    ];

    const state = {
        scope: 'client',
        screen: 'home',
        companies: [],
        companyId: 0,
        workspace: null,
        loading: false,
        error: '',
        notice: '',
        autoImported: {},
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

    function ws() {
        return state.workspace || {};
    }

    function isEmpty() {
        return !state.workspace || state.workspace.empty;
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
        const bank = (state.workspace && state.workspace.bank) || {};
        const docs = (bank.portal_documents || []).filter((row) => !/\.(jpe?g|png|gif|webp|heic)$/i.test(row.name || ''));
        const pick = docs[0] || (bank.portal_documents || [])[0];
        if (!bank.imported && pick && !state.autoImported[state.companyId]) {
            state.autoImported[state.companyId] = true;
            try {
                await postAction('import-statement', { document_id: pick.id });
                const result = (state.workspace && state.workspace.import_result) || {};
                if ((result.imported || 0) > 0) {
                    state.notice = `Used ${pick.name} — imported ${result.imported} line(s).`;
                    if (state.screen === 'home' || state.screen === 'bank' || state.screen === 'file' || state.screen === 'review') {
                        state.screen = 'review';
                    }
                } else {
                    state.notice = `Read ${pick.name}, but there were no new lines to import.`;
                }
            } catch (err) {
                state.error = err.message || 'The statement on file could not be read. Export CSV from the bank, or paste the rows.';
            }
        }
    }

    async function postAction(action, payload, method) {
        const res = await fetch(`${apiBase()}/${state.companyId}/${action}`, {
            method: method || 'POST',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload || {}),
        });
        state.workspace = await readJson(res);
        return state.workspace;
    }

    function yearEndViewName() {
        return state.scope === 'admin' ? 'admin-year-end' : 'client-year-end';
    }

    function accountsViewName() {
        return state.scope === 'admin' ? 'admin-accounts' : 'client-accounts';
    }

    function setScreen(screen) {
        state.screen = screen || 'home';
        const viewName = (screen === 'file' || screen === 'year-end') ? yearEndViewName() : accountsViewName();
        if (typeof syncViewHash === 'function') syncViewHash(viewName);
        document.querySelectorAll('.nav-item, .top-menu-item').forEach((item) => {
            const view = item.getAttribute('data-view');
            item.classList.toggle('active', view === viewName);
        });
        render();
    }

    function headerMeta() {
        const org = ws().organisation;
        if (!org) return 'Upload a bank statement. We turn it into books, then a Companies House pack.';
        const bits = [org.registered_name];
        if (org.company_number) bits.push(`No. ${org.company_number}`);
        if (org.period_end) bits.push(`Year end ${ukDate(org.period_end)}`);
        return bits.join(' · ');
    }

    function renderShell(inner) {
        const companies = state.companies.map((row) => (
            `<option value="${Number(row.id)}" ${Number(row.id) === Number(state.companyId) ? 'selected' : ''}>${h(row.ledger_name || row.name)}${row.company_number ? ` (${h(row.company_number)})` : ''}</option>`
        )).join('');
        const groups = ['Your books', 'Year end', 'More'];
        const tabs = groups.map((group) => {
            const buttons = SCREENS.filter((s) => s.group === group).map((s) => (
                `<button type="button" class="${state.screen === s.id ? 'is-active' : ''}" data-accounts-screen="${s.id}">${h(s.label)}</button>`
            )).join('');
            return `<div class="accounts-file-nav-group"><span>${h(group)}</span><div class="portfolio-filter-bar accounts-file-tabs" role="tablist" aria-label="${h(group)}">${buttons}</div></div>`;
        }).join('');
        const title = (state.screen === 'file' || state.screen === 'year-end') ? 'Year end' : 'Accounts';
        return `
            <div class="page-title-row accounts-file-head">
                <div class="page-title-text">
                    <h1>${title}</h1>
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
            ${state.notice ? `<div class="accounts-file-banner is-ok" role="status">${h(state.notice)}</div>` : ''}
            ${inner}
        `;
    }

    function emptyCompany() {
        return `
            <div class="accounts-file-empty">
                <h2>No company to keep books for</h2>
                <p>Add or register a UK company in Company Registered first. Accounts attach to that company.</p>
            </div>`;
    }

    function loadingState() {
        return `<div class="accounts-file-empty"><p>Loading your books…</p></div>`;
    }

    function nextStepCta() {
        const step = ws().next_step || (isEmpty() ? 'upload' : 'file');
        if (step === 'review') {
            return `<button type="button" class="btn-primary" data-accounts-screen="review">Review categories</button>`;
        }
        if (step === 'file') {
            return `<button type="button" class="btn-primary" data-accounts-screen="file">File at Companies House</button>`;
        }
        if (step === 'import_existing') {
            return `<button type="button" class="btn-primary" data-accounts-action="import-statement">Use the statement already on file</button>`;
        }
        return `<button type="button" class="btn-primary" data-accounts-screen="bank">Upload a bank statement</button>`;
    }

    function bankScreen(data) {
        const bank = (data && data.bank) || {};
        const rec = bank.reconciliation || {};
        const statements = (bank.statements || []).map((row) => (
            `<li><span>${h(row.filename || 'Statement')}</span><strong>${row.row_count} lines</strong></li>`
        )).join('') || '<li>No statement imported into the books yet.</li>';
        const portal = (bank.portal_documents || []).map((row) => (
            `<li><span>${h(row.name)}</span><button type="button" class="btn-secondary btn-table" data-accounts-doc="${row.id}">Use this</button></li>`
        )).join('');
        const recBlock = rec.statement_closing != null ? `
            <div class="accounts-file-grid">
                <section class="accounts-file-card">
                    <h2>Bank reconciliation</h2>
                    <ul class="accounts-bank-list">
                        <li><span>Opening</span><strong>${gbp(rec.statement_opening)}</strong></li>
                        <li><span>Statement closing</span><strong>${gbp(rec.statement_closing)}</strong></li>
                        <li><span>Your books</span><strong>${gbp(rec.ledger_bank)}</strong></li>
                    </ul>
                    <p class="accounts-file-help">${rec.books_agrees ? 'The bank on your books matches the statement.' : 'The bank on your books does not yet match the statement closing balance.'}</p>
                </section>
                <section class="accounts-file-card">
                    <h2>Statements on file</h2>
                    <ul class="accounts-bank-list">${statements}</ul>
                </section>
            </div>` : '';
        return `
            <section class="accounts-file-card">
                <h2>Upload or paste a bank statement</h2>
                <p class="accounts-file-help">CSV from Starling, Tide, Monzo, Barclays, Lloyds, HSBC or any date / description / amount export. PDF works when the bank printed the rows as text. We only ask if a file or a Companies House code is missing.</p>
                <div class="accounts-upload-grid">
                    <label class="accounts-file-drop">
                        <span>CSV or PDF file</span>
                        <input id="accounts-statement-file" type="file" accept=".csv,.txt,.pdf,text/csv,application/pdf">
                    </label>
                    <label>Opening balance (if not in the file)
                        <input id="accounts-opening" class="select-filter" inputmode="decimal" placeholder="12000.00">
                    </label>
                    <label class="accounts-span">Or paste the rows
                        <textarea id="accounts-statement-paste" class="select-filter accounts-textarea" placeholder="Date, Description, Money in, Money out, Balance"></textarea>
                    </label>
                </div>
                <div class="accounts-file-empty-actions">
                    <button type="button" class="btn-primary" data-accounts-action="import-statement">Turn this into books</button>
                    ${(bank.portal_documents || []).length ? '<button type="button" class="btn-primary" data-accounts-action="import-statement">Use the statement already on file</button>' : ''}
                    <button type="button" class="btn-secondary" data-accounts-action="sample-statement">Try a sample statement</button>
                    ${(bank.imported || (bank.statements || []).length) ? '<button type="button" class="btn-secondary" data-accounts-action="reset-books">Start again from the statement</button>' : ''}
                </div>
            </section>
            ${portal ? `<section class="accounts-file-card"><h2>Already on this company file</h2><p class="accounts-file-help">You already uploaded these. We will turn the latest readable one into books — you do not need to upload it again.</p><ul class="accounts-bank-list">${portal}</ul></section>` : ''}
            ${recBlock}`;
    }

    function homeScreen(data) {
        if (isEmpty()) {
            return `
                <div class="accounts-file-empty">
                    <h2>From bank statement to Companies House</h2>
                    <p>${(data.bank && (data.bank.portal_documents || []).length) ? 'You already uploaded a statement in Documents. We will turn that into books — you do not need to upload it again.' : 'Upload last year’s UK bank CSV (or paste it). We categorise the lines, build a profit and loss and balance sheet you can check, then give you a micro-entity pack to send to Companies House.'}</p>
                    <div class="accounts-file-empty-actions">
                        ${nextStepCta()}
                        <button type="button" class="btn-secondary" data-accounts-action="sample-statement">Try a sample statement</button>
                        <button type="button" class="btn-secondary" data-accounts-action="sample">Load sample organisation</button>
                    </div>
                </div>
                ${bankScreen(data)}`;
        }
        const home = data.home || {};
        const items = (home.watch_items || []).map((item) => (
            `<article class="accounts-watch-item is-${h(item.tone)}"><h3>${h(item.title)}</h3><p>${h(item.detail)}</p></article>`
        )).join('');
        return `
            <div class="stats-grid accounts-file-stats">
                <div class="stat-card"><div class="stat-info"><div class="label">Bank</div><div class="value">${gbp(home.bank_total)}</div></div></div>
                <div class="stat-card"><div class="stat-info"><div class="label">Money in</div><div class="value">${gbp(home.money_in)}</div></div></div>
                <div class="stat-card"><div class="stat-info"><div class="label">Money out</div><div class="value">${gbp(home.money_out)}</div></div></div>
                <div class="stat-card"><div class="stat-info"><div class="label">Profit for the year</div><div class="value">${gbp((data.profit_and_loss || {}).profit)}</div></div></div>
            </div>
            <div class="accounts-file-empty-actions" style="margin: 4px 0 12px;">
                ${nextStepCta()}
                <button type="button" class="btn-secondary" data-accounts-screen="reports">See the reports</button>
                <button type="button" class="btn-secondary" data-accounts-action="reset-books">Start again from the statement</button>
            </div>
            <div class="accounts-file-grid">
                <section class="accounts-file-card">
                    <h2>What to do next</h2>
                    <div class="accounts-watch-list">${items}</div>
                </section>
                <section class="accounts-file-card">
                    <h2>Bank</h2>
                    <ul class="accounts-bank-list">
                        ${(home.bank_accounts || []).map((row) => `<li><span>${h(row.name)}</span><strong>${gbp(row.balance)}</strong></li>`).join('') || '<li>No bank balance yet.</li>'}
                    </ul>
                </section>
            </div>`;
    }

    function categoryOptions(selected) {
        const cats = ((ws().bank || {}).categories) || [];
        return cats.map((cat) => (
            `<option value="${h(cat.code)}" ${cat.code === selected ? 'selected' : ''}>${h(cat.label)}</option>`
        )).join('');
    }

    function reviewScreen(data) {
        if (isEmpty()) {
            return `<div class="accounts-file-empty"><h2>Nothing to review yet</h2><p>${(data.bank && (data.bank.portal_documents || []).length) ? 'A statement is already on this company file. Use it to build the books, then check the categories here.' : 'Upload a bank statement first. We only ask you to confirm categories we are unsure about.'}</p><div class="accounts-file-empty-actions">${nextStepCta()}</div></div>`;
        }
        const bank = data.bank || {};
        const rec = bank.reconciliation || {};
        const rows = (bank.lines || []).map((line) => `
            <tr class="${line.needs_review ? 'is-review' : ''}">
                <td>${h(ukDate(line.date))}</td>
                <td>${h(line.description)}</td>
                <td class="num">${line.money_in ? gbp(line.money_in) : ''}</td>
                <td class="num">${line.money_out ? gbp(line.money_out) : ''}</td>
                <td>
                    <select class="select-filter" data-recat-id="${line.id}">
                        ${categoryOptions(line.category_code)}
                    </select>
                </td>
            </tr>
        `).join('') || '<tr><td colspan="5">No bank lines yet.</td></tr>';
        const pl = data.profit_and_loss || {};
        const tb = data.trial_balance || {};
        const bs = data.balance_sheet || {};
        return `
            <div class="stats-grid accounts-file-stats">
                <div class="stat-card"><div class="stat-info"><div class="label">To check</div><div class="value">${bank.needs_review || 0}</div></div></div>
                <div class="stat-card"><div class="stat-info"><div class="label">Profit</div><div class="value">${gbp(pl.profit)}</div></div></div>
                <div class="stat-card"><div class="stat-info"><div class="label">Bank on the books</div><div class="value">${gbp((rec.ledger_bank))}</div></div></div>
                <div class="stat-card"><div class="stat-info"><div class="label">Trial balance</div><div class="value">${tb.agrees ? 'Agrees' : 'Out'}</div></div></div>
            </div>
            <section class="accounts-file-card">
                <h2>Check each line</h2>
                <p class="accounts-file-help">Change anything that looks wrong. Sales, wages, rent and similar labels are enough — you do not need accountancy codes. Saving a category updates the profit and loss and balance sheet.</p>
                <div class="data-table-container">
                    <table class="data-table">
                        <thead><tr><th>Date</th><th>What the bank says</th><th>In</th><th>Out</th><th>Category</th></tr></thead>
                        <tbody>${rows}</tbody>
                    </table>
                </div>
                <div class="accounts-file-empty-actions">
                    <button type="button" class="btn-secondary" data-accounts-action="confirm-review">Looks right — continue</button>
                    <button type="button" class="btn-primary" data-accounts-screen="file">File at Companies House</button>
                </div>
            </section>
            <p class="accounts-file-help">${bs.balances ? 'The balance sheet agrees: what the company owns equals capital and reserves.' : `Balance sheet is out by ${gbp(bs.difference)}.`}</p>`;
    }

    function reportsScreen(data) {
        if (isEmpty()) {
            return `<div class="accounts-file-empty"><h2>No reports yet</h2><p>Upload a statement so we can build a profit and loss, balance sheet and trial balance.</p></div>`;
        }
        const org = data.organisation || {};
        const plRows = ((data.profit_and_loss || {}).lines || []).map((line) => (
            `<tr class="${line.is_total ? 'is-total' : ''}"><td>${h(line.label)}</td><td class="num">${gbp(line.current)}</td><td class="num">${gbp(line.prior)}</td></tr>`
        )).join('');
        const bsRows = ((data.balance_sheet || {}).lines || []).map((line) => (
            `<tr class="${line.is_total ? 'is-total' : ''}"><td>${h(line.code)}. ${h(line.label)}</td><td class="num">${gbp(line.current)}</td><td class="num">${gbp(line.prior)}</td></tr>`
        )).join('');
        const tb = data.trial_balance || { rows: [] };
        const tbRows = (tb.rows || []).map((row) => (
            `<tr><td>${h(row.code)}</td><td>${h(row.name)}</td><td class="num">${row.debit ? gbp(row.debit) : ''}</td><td class="num">${row.credit ? gbp(row.credit) : ''}</td></tr>`
        )).join('');
        return `
            <section class="accounts-file-card">
                <h2>Profit and loss</h2>
                <p class="accounts-file-help">What came in and what went out in the year ended ${h(ukDate(org.period_end))}.</p>
                <div class="data-table-container"><table class="data-table accounts-report-table"><thead><tr><th></th><th>This year</th><th>Last year</th></tr></thead><tbody>${plRows}</tbody></table></div>
            </section>
            <section class="accounts-file-card">
                <h2>Balance sheet</h2>
                <p class="accounts-file-help">What the company owns and owes on ${h(ukDate(org.period_end))}.</p>
                <div class="data-table-container"><table class="data-table accounts-report-table"><thead><tr><th></th><th>This year</th><th>Last year</th></tr></thead><tbody>${bsRows}</tbody></table></div>
            </section>
            <section class="accounts-file-card">
                <h2>Trial balance</h2>
                <p class="accounts-file-help">${tb.agrees ? 'Debits equal credits, so the books are in balance.' : `The books are out by ${gbp(tb.difference)}.`}</p>
                <div class="data-table-container">
                    <table class="data-table">
                        <thead><tr><th>Code</th><th>Account</th><th>Debit</th><th>Credit</th></tr></thead>
                        <tbody>${tbRows}<tr class="is-total"><td></td><td>Totals</td><td class="num">${gbp(tb.total_debit)}</td><td class="num">${gbp(tb.total_credit)}</td></tr></tbody>
                    </table>
                </div>
            </section>`;
    }

    function fileScreen(data) {
        if (isEmpty()) {
            return `<div class="accounts-file-empty"><h2>No accounts to file yet</h2><p>${(data.bank && (data.bank.portal_documents || []).length) ? 'A statement is already on this company file. Turn it into books first, then you can send the pack to Companies House.' : 'Upload a bank statement, check the review screen, then come back here to send the pack to Companies House.'}</p><div class="accounts-file-empty-actions">${nextStepCta()}</div></div>`;
        }
        const ye = data.year_end || {};
        const org = data.organisation || {};
        const filing = data.filing || {};
        const submit = data.ch_submit;
        const needed = (filing.needed || []).map((item) => (
            `<li><strong>${h(item.label)}</strong> — ${h(item.reason)}</li>`
        )).join('');
        const history = (filing.last_filings || []).map((row) => (
            `<li><span>${h(row.mode)} · ${h(ukDate(row.created_at))}</span><strong>${h(row.receipt || '')}</strong></li>`
        )).join('') || '<li>Nothing sent yet.</li>';
        const size = ye.size || { details: [] };
        return `
            <div class="accounts-file-banner ${ye.can_file_micro ? 'is-ok' : 'is-warn'}">
                <strong>${ye.can_file_micro ? 'These books can be filed as FRS 105 micro-entity accounts.' : 'These books cannot currently be filed as a micro-entity.'}</strong>
                <p>Deadline ${h(ukDate(org.filing_due))}. ${h(org.filing_due_note || '')}</p>
            </div>
            ${submit ? `<div class="accounts-file-banner is-ok"><strong>${submit.mode === 'live' ? 'Sent to Companies House' : 'Sandbox receipt'}</strong><p>${h(submit.message)}</p><p>Receipt <code>${h(submit.receipt)}</code></p></div>` : ''}
            <section class="accounts-file-card">
                <h2>Send to Companies House</h2>
                <p class="accounts-file-help">Two ways, both valid. Download the pack and upload it in WebFiling yourself, or let this software send it if you have a company authentication code and (optionally) a presenter / gateway login. Missing secrets never block you — we issue a sandbox receipt and keep the download.</p>
                <ol class="accounts-file-steps">
                    <li>Download the <strong>Companies House pack</strong> (filleted HTML).</li>
                    <li>Open <a href="${h(filing.webfiling_url || 'https://ewf.companieshouse.gov.uk/')}" target="_blank" rel="noopener">Companies House WebFiling</a> and upload that file.</li>
                    <li>Or fill the boxes below and press <strong>Send to Companies House</strong>.</li>
                </ol>
                <div class="accounts-file-empty-actions">
                    <button type="button" class="btn-primary" data-accounts-export="filleted">Download Companies House pack</button>
                    <button type="button" class="btn-secondary" data-accounts-export="members">Download members’ pack</button>
                    <button type="button" class="btn-secondary" data-accounts-export="ixbrl">Draft iXBRL</button>
                    <a class="btn-secondary" href="${h(filing.webfiling_url || 'https://ewf.companieshouse.gov.uk/')}" target="_blank" rel="noopener">Open WebFiling</a>
                </div>
                ${needed ? `<div class="accounts-needed"><p>Provide only if you have them:</p><ul>${needed}</ul></div>` : ''}
                <div class="accounts-org-grid" style="margin-top:12px;">
                    <label>Company number <input id="acc-ch-number" class="select-filter" value="${h(filing.company_number || org.company_number || '')}" maxlength="8" placeholder="14122819"></label>
                    <label>Authentication code <input id="acc-ch-auth" class="select-filter" type="password" autocomplete="off" placeholder="${filing.has_authentication_code ? 'On file — paste to replace' : 'From your CH correspondence'}"></label>
                    <label>Presenter ID (software filing) <input id="acc-ch-presenter" class="select-filter" placeholder="Optional"></label>
                    <label>Presenter password / API key <input id="acc-ch-key" class="select-filter" type="password" autocomplete="off" placeholder="Optional"></label>
                </div>
                <div class="accounts-file-empty-actions">
                    <button type="button" class="btn-primary" data-accounts-action="submit-ch">Send to Companies House</button>
                </div>
            </section>
            <section class="accounts-file-card">
                <h2>Size test (plain English)</h2>
                <p class="accounts-file-help">${size.from_6_apr_2025 ? 'Year beginning on or after 6 April 2025: turnover £1m, balance sheet total £500k, 10 employees.' : 'Year beginning before 6 April 2025: turnover £632k, balance sheet total £316k, 10 employees.'} Meet any two of the three.</p>
                <div class="data-table-container">
                    <table class="data-table">
                        <thead><tr><th>Condition</th><th>This year</th><th>Limit</th><th></th></tr></thead>
                        <tbody>${(size.details || []).map((row) => `<tr><td>${h(row.label)}</td><td class="num">${row.label === 'Average employees' ? String(row.actual) : gbp(row.actual)}</td><td class="num">${row.label === 'Average employees' ? `≤ ${row.limit}` : `≤ ${gbp(row.limit)}`}</td><td>${row.met ? 'Met' : 'Not met'}</td></tr>`).join('')}</tbody>
                    </table>
                </div>
            </section>
            <section class="accounts-file-card">
                <h2>Previous sends</h2>
                <ul class="accounts-bank-list">${history}</ul>
                <p class="accounts-file-help">${h(ye.ixbrl_gap || '')}</p>
            </section>`;
    }

    function displayBalance(acc) {
        if (['INCOME', 'EQUITY', 'CURRENT_LIABILITY', 'LONG_TERM_LIABILITY', 'PROVISION', 'FIXED_ASSET_CONTRA'].includes(acc.account_type)) {
            return gbp(Number(acc.ytd_credit || 0) - Number(acc.ytd_debit || 0));
        }
        return gbp(acc.ytd_net);
    }

    function coaScreen(data) {
        const rows = (data.accounts || []).map((acc) => `
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
                <p class="accounts-file-help">Background only. You do not need this screen to file.</p>
                <div class="data-table-container">
                    <table class="data-table">
                        <thead><tr><th>Code</th><th>Account</th><th>Type</th><th>YTD</th><th>Last year</th><th></th></tr></thead>
                        <tbody>${rows || '<tr><td colspan="6">No nominal codes yet.</td></tr>'}</tbody>
                    </table>
                </div>
            </section>`;
    }

    function journalsScreen(data) {
        const options = (data.accounts || []).map((acc) => `<option value="${h(acc.code)}">${h(acc.code)} ${h(acc.name)}</option>`).join('');
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
        const posted = (data.journals || []).map((jnl) => {
            const lines = (jnl.lines || []).map((line) => (
                `<tr><td>${h(line.code)}</td><td>${h(line.name)}</td><td>${h(line.description)}</td><td class="num">${line.debit ? gbp(line.debit) : ''}</td><td class="num">${line.credit ? gbp(line.credit) : ''}</td></tr>`
            )).join('');
            return `<article class="accounts-journal">
                <header><strong>${h(jnl.reference || 'Journal')}</strong> · ${h(ukDate(jnl.journal_date))} · ${h(jnl.narration)}</header>
                <table class="data-table"><thead><tr><th>Code</th><th>Account</th><th>Description</th><th>Debit</th><th>Credit</th></tr></thead><tbody>${lines}</tbody></table>
            </article>`;
        }).join('') || '<p class="accounts-file-help">No journals posted yet.</p>';
        return `
            <section class="accounts-file-card">
                <h2>Post a manual journal</h2>
                <p class="accounts-file-help">Optional. Use this for a year-end adjustment the bank statement does not show.</p>
                <div class="accounts-journal-form">
                    <label>Date <input id="accounts-jnl-date" type="date" class="select-filter" value="${h((data.organisation || {}).period_end || '')}"></label>
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

    function organisationScreen(data) {
        const org = data.organisation || {};
        const directors = (org.directors && org.directors.length ? org.directors : [{ name: '', role: 'Director' }]);
        const directorFields = directors.map((d, i) => `
            <div class="accounts-director-row">
                <input class="select-filter" data-director="${i}" data-dfield="name" value="${h(d.name)}" placeholder="Director name">
                <input class="select-filter" data-director="${i}" data-dfield="role" value="${h(d.role || 'Director')}" placeholder="Role">
            </div>
        `).join('');
        const exclusions = (data.year_end && data.year_end.exclusions) || [];
        const exclusionBoxes = exclusions.map((item) => `
            <label class="accounts-check"><input type="checkbox" data-exclusion="${h(item.key)}" ${item.flagged ? 'checked' : ''}> ${h(item.label)}</label>
        `).join('');
        const notes = org.notes || {};
        return `
            <section class="accounts-file-card">
                <h2>Company details</h2>
                <p class="accounts-file-help">These names and dates print on the accounts pack. Add the company number if it is missing.</p>
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
                    <label class="accounts-check accounts-span"><input id="acc-org-first" type="checkbox" ${org.is_first_accounts ? 'checked' : ''}> First accounting period</label>
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
                    <button type="button" class="btn-primary" data-accounts-action="save-org">Save company details</button>
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

    function tbScreen(data) {
        const tb = data.trial_balance || { rows: [] };
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
                <p class="accounts-file-help">${tb.agrees ? 'The trial balance agrees.' : `The trial balance is out by ${gbp(tb.difference)}.`}</p>
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

    function yearEndScreen(data) {
        const ye = data.year_end || {};
        const statements = (ye.statements || []).map((st) => (
            `<article class="accounts-file-note"><h3>${h(st.title)}</h3><p>${h(st.body)}</p><p class="accounts-file-help">${h(st.law)}</p></article>`
        )).join('');
        const notes = (ye.notes || []).map((note) => (
            `<article class="accounts-file-note"><h3>${h(note.title)}</h3><p>${h(note.body)}</p><p class="accounts-file-help">${h(note.law)}</p></article>`
        )).join('');
        return `
            <section class="accounts-file-card">
                <h2>Required notes</h2>
                ${notes}
            </section>
            <section class="accounts-file-card">
                <h2>Director and audit-exemption statements</h2>
                ${statements}
                <p class="accounts-file-help">${h(ye.filing_note || '')}</p>
                <div class="accounts-file-empty-actions">
                    <button type="button" class="btn-primary" data-accounts-screen="file">Go to File at Companies House</button>
                </div>
            </section>`;
    }

    function innerContent() {
        if (state.loading) return loadingState();
        if (!state.companyId) return emptyCompany();
        const data = ws();
        const org = data.organisation || {};
        if (state.screen === 'home') return homeScreen(data);
        if (state.screen === 'bank') return bankScreen(data);
        if (state.screen === 'review') return reviewScreen(data);
        if (state.screen === 'reports') return reportsScreen(data);
        if (state.screen === 'file') return fileScreen(data);
        if (isEmpty()) {
            return `<div class="accounts-file-empty"><h2>Upload a statement first</h2><p>These extra screens fill in after the bank statement is on the books.</p><div class="accounts-file-empty-actions">${nextStepCta()}</div></div>`;
        }
        if (state.screen === 'coa') return coaScreen(data);
        if (state.screen === 'journals') return journalsScreen(data);
        if (state.screen === 'organisation') return organisationScreen(data);
        if (state.screen === 'pl') {
            return reportScreen(
                'Profit and loss',
                `Year ended ${ukDate(org.period_end)}.`,
                (data.profit_and_loss || {}).lines,
            );
        }
        if (state.screen === 'bs') {
            const extra = (data.balance_sheet || {}).balances
                ? '<p class="accounts-file-help">The balance sheet agrees: net assets equal capital and reserves.</p>'
                : `<p class="dash-banner-error">Balance sheet out by ${gbp((data.balance_sheet || {}).difference)}.</p>`;
            return reportScreen('Balance sheet', `As at ${ukDate(org.period_end)}.`, (data.balance_sheet || {}).lines, extra);
        }
        if (state.screen === 'tb') return tbScreen(data);
        if (state.screen === 'year-end') return yearEndScreen(data);
        return homeScreen(data);
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
        root.querySelectorAll('[data-accounts-doc]').forEach((btn) => {
            btn.addEventListener('click', async () => {
                state.error = '';
                state.notice = '';
                try {
                    await handleImportStatement(Number(btn.getAttribute('data-accounts-doc')));
                } catch (err) {
                    state.error = err.message || 'Could not read that statement.';
                }
                render();
            });
        });
        const fileInput = document.getElementById('accounts-statement-file');
        if (fileInput) {
            fileInput.addEventListener('change', async () => {
                if (!(fileInput.files && fileInput.files[0])) return;
                state.error = '';
                state.notice = '';
                try {
                    await handleImportStatement();
                } catch (err) {
                    state.error = err.message || 'Could not read that statement.';
                }
                render();
            });
        }
        root.querySelectorAll('[data-jline]').forEach((input) => {
            input.addEventListener('input', () => {
                const i = Number(input.getAttribute('data-jline'));
                const field = input.getAttribute('data-jfield');
                if (!state.journalLines[i]) state.journalLines[i] = { code: '', debit: '', credit: '', description: '' };
                state.journalLines[i][field] = input.value;
            });
        });
        root.querySelectorAll('[data-recat-id]').forEach((selectEl) => {
            selectEl.addEventListener('change', () => handleRecategorise(selectEl.getAttribute('data-recat-id'), selectEl.value));
        });
    }

    async function handleRecategorise(lineId, code) {
        state.error = '';
        try {
            await postAction('recategorise', { line_id: Number(lineId), category_code: code });
            state.notice = 'Category saved.';
        } catch (err) {
            state.error = err.message || 'Could not save that category.';
        }
        render();
    }

    async function handleImportStatement(documentId) {
        if (!state.companyId) throw new Error('Select a company first.');
        const fileInput = document.getElementById('accounts-statement-file');
        const paste = ((document.getElementById('accounts-statement-paste') || {}).value || '').trim();
        const opening = ((document.getElementById('accounts-opening') || {}).value || '').trim();
        const file = fileInput && fileInput.files && fileInput.files[0];
        if (file) {
            const form = new FormData();
            form.append('file', file, file.name);
            if (opening) form.append('opening_balance', opening);
            if (paste) form.append('csv_text', paste);
            const res = await fetch(`${apiBase()}/${state.companyId}/import-statement`, {
                method: 'POST',
                credentials: 'same-origin',
                body: form,
            });
            state.workspace = await readJson(res);
        } else if (paste) {
            await postAction('import-statement', {
                csv_text: paste,
                opening_balance: opening,
                filename: 'pasted-statement.csv',
            });
        } else {
            const payload = { opening_balance: opening };
            if (documentId) payload.document_id = documentId;
            await postAction('import-statement', payload);
        }
        const result = (state.workspace && state.workspace.import_result) || {};
        state.notice = `Imported ${result.imported || 0} line(s)` + (result.skipped ? `, skipped ${result.skipped} duplicate(s)` : '') + '.';
        if ((result.imported || 0) > 0 || (ws().bank || {}).imported) {
            state.screen = 'review';
        }
    }

    async function handleAction(action) {
        state.error = '';
        state.notice = '';
        try {
            if (action === 'sample') {
                await postAction('sample', {});
                state.screen = 'home';
            } else if (action === 'sample-statement') {
                await postAction('sample-statement', {});
                state.notice = 'Sample statement loaded. Check Review, then file.';
                state.screen = 'review';
            } else if (action === 'reset-books') {
                delete state.autoImported[state.companyId];
                await postAction('reset-books', {});
                await loadWorkspace();
                state.notice = 'Books cleared. Using the statement already on file.';
            } else if (action === 'import-statement') {
                await handleImportStatement();
            } else if (action === 'confirm-review') {
                await postAction('confirm-review', {});
                state.notice = 'Review marked as done.';
                state.screen = 'file';
            } else if (action === 'submit-ch') {
                await postAction('submit-companies-house', {
                    company_number: (document.getElementById('acc-ch-number') || {}).value,
                    authentication_code: (document.getElementById('acc-ch-auth') || {}).value,
                    presenter_id: (document.getElementById('acc-ch-presenter') || {}).value,
                    presenter_auth: (document.getElementById('acc-ch-key') || {}).value,
                    pack_kind: 'filleted',
                });
                const submit = (state.workspace && state.workspace.ch_submit) || {};
                state.notice = submit.receipt ? `Receipt ${submit.receipt}` : (submit.message || 'Filing recorded.');
                state.screen = 'file';
            } else if (action === 'start') {
                await postAction('start', {});
                state.screen = 'bank';
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
        if (screen === 'year-end') state.screen = 'file';
        else state.screen = screen || 'home';
        state.loading = true;
        state.error = '';
        state.notice = '';
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
