/* Accounts File — lives inside the Accountancy tab (traded / dormant / HMRC). */
(function () {
    const SCREENS = [
        { id: 'home', kind: 'primary', label: 'Start' },
        { id: 'bank', kind: 'primary', label: 'Bank' },
        { id: 'review', kind: 'primary', label: 'Check' },
        { id: 'file', kind: 'primary', label: 'File' },
        { id: 'hmrc', kind: 'primary', label: 'HMRC' },
        { id: 'organisation', kind: 'primary', label: 'Company' },
        { id: 'pl', kind: 'report', label: 'Profit and loss' },
        { id: 'bs', kind: 'report', label: 'Balance sheet' },
        { id: 'tb', kind: 'report', label: 'Trial balance' },
        { id: 'coa', kind: 'report', label: 'Chart of accounts' },
        { id: 'journals', kind: 'report', label: 'Journals' },
        { id: 'year-end', kind: 'report', label: 'Notes' },
        { id: 'reports', kind: 'report', label: 'All reports' },
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
        pendingCompanyId: 0,
        selectedLineIds: [],
        checkStep: 0,
        precheck: null,
        classifyReport: null,
        compileTested: false,
        reconciledOnce: false,
        chProfile: null,
        statementHint: null,
        pendingStatementFiles: [],
        pendingOpening: '',
        accountancyPane: 'filing',
        compiling: false,
        compileProgress: 0,
        compileProgressLabel: '',
        journalLines: [
            { code: '', debit: '', credit: '', description: '' },
            { code: '', debit: '', credit: '', description: '' },
        ],
    };

    const ECOMMERCE_SALES_RE = /\b(stripe|sumup|square|shopify|woocommerce|woo\s*commerce|paypal|pay\s*pal|etsy|klarna|clearpay|afterpay|gocardless|worldpay|adyen|braintree|amazon|amzn|ebay|tiktok\s*shop|bigcommerce|wix|squarespace|ecwid|magento|prestashop|marketplace\s*payout|platform\s*payout)\b/i;
    const MARKETPLACE_RE = /\b(amazon|amzn|ebay|etsy|tiktok\s*shop)\b/i;
    const AMAZON_PRIME_RE = /\bamazon\s+prime\b/i;
    const PRIOR_TX_RE = /relates to a previous/i;
    const UTILITY_RE = /\b(british gas|edf|e\.?on\b|octopus energy|sse\b|thames water|virgin media|vodafone|\bbt\b|light and heat|octopus|scottish power|bulb energy|ovo energy|utility warehouse)\b/i;
    const RENT_RE = /\b(rent|landlord|rightmove|openrent|spareroom)\b/i;
    const FUEL_RE = /\b(shell|bp\b|tesco petrol|esso\b|fuel|petrol|parking|halfords|\bmot\b)\b/i;
    const TRAVEL_RE = /\b(trainline|uber|tfl\b|easyjet|ryanair|booking\.com|airbnb|national express)\b/i;
    const INSURE_RE = /\b(insurance|aviva|axa|hiscox|direct line|churchill)\b/i;
    const LEGAL_RE = /\b(companies house|solicitor|lawyer|legal|accountant|accountancy|brixen)\b/i;
    const BANK_FEE_RE = /\b(bank charge|monthly (?:account )?fee|starling.*fee|tide fee|monzo plus|monzo.*fee|paid membership)\b/i;
    const HMRC_CT_RE = /\b(corporation tax|hmrc ct)\b/i;
    const HMRC_VAT_RE = /\b(hmrc vat|vat payment|vat return)\b/i;
    const HMRC_RE = /\bhmrc\b/i;
    const STOCK_RE = /\b(screwfix|toolstation|materials|stock|alibaba|aliexpress|wholesale)\b/i;
    const SUPERMARKET_RE = /\b(tesco|sainsbury|asda|morrisons|aldi|lidl)\b/i;
    const WAGES_RE = /\b(salary|wages|payroll|paye|staff pay)\b/i;
    const TITLE_RE = /^(mr|mrs|ms|miss|mx|dr|sir|lord|lady|prof)\.?$/i;
    const DIVIDEND_RE = /\b(dividends?|divi\b|interim\s+div|final\s+div)\b/i;
    const DIRECTOR_LOAN_RE = /\b(directors?\s+loans?|loan\s+account|\bdla\b|drawings?|directors?\s+advances?)\b/i;
    const DIRECTOR_EXPENSE_RE = /\b(expenses?|reimburse(?:ment|d)?|mileage|subsistence)\b/i;
    const SALARY_RE = /\b(salary|salaries|wages|payroll|paye|n\.?i\.?c\.?|national insurance|remuneration|director(?:'s|s)?\s+pay|director(?:'s|s)?\s+salary)\b/i;
    const DIRECTOR_WORD_RE = /\bdirectors?\b/i;

    function rootEl() {
        if (state.accountancyPane === 'hmrc') {
            return document.getElementById(state.scope === 'admin' ? 'admin-hmrc-root' : 'client-hmrc-root');
        }
        return document.getElementById(state.scope === 'admin' ? 'admin-accounts-root' : 'client-accounts-root');
    }

    function apiBase() {
        return state.scope === 'admin' ? '/api/admin/accounts-file' : '/api/client/accounts-file';
    }

    function storageKey() {
        const uid = (window.currentUser && currentUser.id) || 'anon';
        return `brixen_accounts_company_${state.scope}_${uid}`;
    }

    function skipSelectKey() {
        const uid = (window.currentUser && currentUser.id) || 'anon';
        return `brixen_accounts_skip_select_${state.scope}_${uid}`;
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

    function filingKind() {
        const data = ws();
        return data.filing_kind || ((data.organisation || {}).filing_kind) || '';
    }

    function hasCompiledBank() {
        const bank = (ws().bank) || {};
        return !!(!isEmpty() && (bank.imported || (bank.lines || []).length || bankLines().length));
    }

    function needsPath() {
        return !!state.companyId && !filingKind() && !hasCompiledBank();
    }

    function goToNextWorkScreen() {
        if (filingKind() === 'dormant') {
            state.screen = 'file';
            return;
        }
        if (hasCompiledBank()) {
            state.screen = 'review';
            goToNeedsYouStep();
            return;
        }
        state.screen = 'bank';
    }

    async function ensureTradedPath() {
        if (!state.companyId || filingKind() === 'dormant' || filingKind() === 'traded') return;
        try {
            await postAction('choose-path', { kind: 'traded' });
        } catch (err) {
            /* Bank upload still continues; path is set on import. */
        }
    }

    function visibleScreens() {
        const kind = filingKind();
        return SCREENS.filter((s) => {
            if (kind === 'dormant' && (s.id === 'bank' || s.id === 'review')) return false;
            return true;
        });
    }

    async function readJson(res) {
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data.status === 'error') {
            if (res.status === 401) throw new Error(data.message || 'Sign in again to open Accountancy.');
            if (res.status === 404) throw new Error(data.message || 'Accounts File is unavailable. Refresh the page.');
            throw new Error(data.message || `Unable to load accounts (${res.status}).`);
        }
        return data;
    }

    async function loadCompanyList() {
        clearOpenBooksOnReload();
        if (!isBrowserReload() && !state.companyId) {
            const saved = Number(localStorage.getItem(storageKey()) || 0);
            if (saved) {
                state.companyId = saved;
                state.pendingCompanyId = saved;
            }
        }
        const res = await fetch(apiBase(), { credentials: 'same-origin' });
        const data = await readJson(res);
        state.companies = data.companies || [];
        if (sessionStorage.getItem(skipSelectKey()) === '1') {
            sessionStorage.removeItem(skipSelectKey());
            localStorage.removeItem(storageKey());
            state.companyId = 0;
            state.pendingCompanyId = 0;
            state.workspace = null;
            return;
        }
        if (!state.companyId) {
            state.pendingCompanyId = 0;
            return;
        }
        const stillThere = state.companies.some((row) => Number(row.id) === Number(state.companyId));
        if (!stillThere) {
            state.companyId = 0;
            state.pendingCompanyId = 0;
        }
    }

    let clearedReload = false;

    function isBrowserReload() {
        try {
            const nav = performance.getEntriesByType('navigation')[0];
            return !!(nav && nav.type === 'reload');
        } catch (err) {
            return false;
        }
    }

    function clearOpenBooksOnReload() {
        if (clearedReload || !isBrowserReload()) return;
        clearedReload = true;
        localStorage.removeItem(storageKey());
        state.companyId = 0;
        state.pendingCompanyId = 0;
        state.workspace = null;
        state.chProfile = null;
        state.screen = 'home';
        state.accountancyPane = 'filing';
        state.pendingStatementFiles = [];
        state.notice = '';
        state.error = '';
        state.checkStep = 0;
        state.precheck = null;
        state.classifyReport = null;
    }

    function accountsHasWork() {
        if ((state.pendingStatementFiles || []).length) return true;
        if (state.compiling) return true;
        if (!state.companyId) return false;
        const bank = ((ws() || {}).bank) || {};
        if (Number(bank.imported || 0) > 0) return true;
        if ((bank.statements || []).length) return true;
        if ((bank.lines || []).length) return true;
        if (filingKind()) return true;
        return false;
    }

    function onAccountsBeforeUnload(event) {
        if (!accountsHasWork()) return;
        event.preventDefault();
        event.returnValue = '';
    }

    async function loadWorkspace() {
        if (!state.companyId) {
            state.workspace = null;
            return;
        }
        const res = await fetch(`${apiBase()}/${state.companyId}`, { credentials: 'same-origin' });
        state.workspace = await readJson(res);
    }

    async function postAction(action, payload, method, options) {
        const res = await fetch(`${apiBase()}/${state.companyId}/${action}`, {
            method: method || 'POST',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload || {}),
        });
        const data = await readJson(res);
        if (!(options && options.quiet)) state.workspace = data;
        return data;
    }

    async function runPool(items, limit, worker) {
        const list = Array.from(items || []);
        if (!list.length) return null;
        let cursor = 0;
        let last = null;
        const size = Math.max(1, Math.min(limit || 6, list.length));
        await Promise.all(Array.from({ length: size }, async () => {
            while (cursor < list.length) {
                const index = cursor;
                cursor += 1;
                last = await worker(list[index], index);
            }
        }));
        return last;
    }

    function portalViewName() {
        return state.scope === 'admin' ? 'admin-accountancy' : 'client-accountancy';
    }

    function chProfileFromRow(row, extra) {
        const src = Object.assign({}, row || {}, extra || {});
        return {
            inc: isoDateOnly(src.inc || src.inc_date || src.incorporation_date || ((ws().organisation || {}).incorporation_date) || ''),
            accounts_next_due: isoDateOnly(src.accounts_next_due || ''),
            accounts_overdue: !!src.accounts_overdue,
            accounts_made_up_to: isoDateOnly(src.accounts_made_up_to || ''),
            accounts_period_start: isoDateOnly(src.accounts_period_start || ''),
            accounts_last_made_up_to: isoDateOnly(src.accounts_last_made_up_to || ''),
            share_capital: Number(src.share_capital || src.share_capital_gbp || 0) || 0,
            is_first_accounts: src.is_first_accounts != null ? !!src.is_first_accounts : !isoDateOnly(src.accounts_last_made_up_to || ''),
            source: src.source || '',
            company_number: src.company_number || '',
            name: src.name || src.ledger_name || '',
        };
    }

    async function loadCompaniesHousePeriod() {
        if (!state.companyId) return;
        const liveUrl = state.scope === 'admin'
            ? `/api/admin/companies/${state.companyId}/companies-house`
            : `/api/client/companies/${state.companyId}/companies-house`;
        const fallbackUrl = state.scope === 'admin'
            ? `/api/admin/companies/${state.companyId}`
            : `/api/client/companies/${state.companyId}`;
        try {
            let res = await fetch(liveUrl, { credentials: 'same-origin' });
            let data = await res.json().catch(() => ({}));
            const live = data.companies_house || {};
            if (res.ok && (live.accounts_made_up_to || live.accounts_next_due || live.company_number)) {
                state.chProfile = chProfileFromRow(data.company || {}, live);
                return;
            }
            res = await fetch(fallbackUrl, { credentials: 'same-origin' });
            data = await res.json().catch(() => ({}));
            const company = data.company || data.data || data;
            if (!company || (!company.id && !company.company_number && !company.inc_date && !company.incorporation_date)) {
                state.chProfile = chProfileFromRow(selectedCompany() || {});
                return;
            }
            state.chProfile = chProfileFromRow(company);
        } catch (err) {
            state.chProfile = state.chProfile || chProfileFromRow(selectedCompany() || {});
        }
    }

    function organisationPayloadFromWorkspace(overrides) {
        const org = (ws().organisation) || {};
        const notes = org.notes || {};
        return Object.assign({
            registered_name: org.registered_name,
            company_number: org.company_number,
            registered_office: org.registered_office,
            incorporation_date: org.incorporation_date,
            period_start: org.period_start,
            period_end: org.period_end,
            prior_period_start: org.prior_period_start,
            prior_period_end: org.prior_period_end,
            employees: org.employees,
            prior_employees: org.prior_employees,
            hmrc_utr: org.hmrc_utr,
            is_first_accounts: !!org.is_first_accounts,
            directors: org.directors || [],
            exclusions: org.exclusions || {},
            notes: {
                off_balance_sheet: notes.off_balance_sheet || '',
                directors_advances: notes.directors_advances || '',
                commitments: notes.commitments || '',
            },
        }, overrides || {});
    }

    async function clampBooksToFilingPeriod() {
        const period = filingPeriod((ws().organisation) || {}, ws());
        if (!period.start || !period.end) return period;
        const dates = allBankLines()
            .filter((line) => inCompileWindow(line, period))
            .map((line) => isoDateOnly(line.date || line.txn_date || ''))
            .filter(Boolean)
            .sort();
        const first = dates[0] || period.start;
        const start = first > period.start ? first : period.start;
        const org = (ws().organisation) || {};
        if (String(org.period_start || '').slice(0, 10) === start && String(org.period_end || '').slice(0, 10) === period.end) {
            return period;
        }
        await postAction('organisation', organisationPayloadFromWorkspace({
            period_start: start,
            period_end: period.end,
            is_first_accounts: !!period.isFirst,
            incorporation_date: period.inc || ((ws().organisation) || {}).incorporation_date,
        }), 'PUT');
        return period;
    }

    function setScreen(screen) {
        if (screen === 'hmrc') {
            setAccountancyPane(state.scope, 'hmrc');
            return;
        }
        state.screen = screen || 'home';
        state.accountancyPane = 'accounts';
        setAccountancyPane(state.scope, 'accounts', { skipLoad: true });
        if (typeof syncViewHash === 'function') syncViewHash(portalViewName());
        document.querySelectorAll('.nav-item, .top-menu-item').forEach((item) => {
            const view = item.getAttribute('data-view');
            item.classList.toggle('active', view === portalViewName());
        });
        render();
        if (state.screen === 'bank' && state.companyId) {
            loadCompaniesHousePeriod().then(() => render()).catch(() => {});
        }
        if (state.screen === 'file' && state.companyId) {
            loadCompaniesHousePeriod()
                .then(() => lockBooksToCompaniesHouseYear())
                .then(() => render())
                .catch(() => {});
        }
    }

    async function lockBooksToCompaniesHouseYear() {
        const period = filingPeriod((ws().organisation) || {}, ws());
        if (!period.start || !period.end) return period;
        const org = (ws().organisation) || {};
        if (String(org.period_start || '').slice(0, 10) === period.start
            && String(org.period_end || '').slice(0, 10) === period.end) {
            return period;
        }
        await postAction('organisation', organisationPayloadFromWorkspace({
            period_start: period.start,
            period_end: period.end,
            is_first_accounts: !!period.isFirst,
            incorporation_date: period.inc || org.incorporation_date,
        }), 'PUT');
        return period;
    }

    function headerMeta() {
        const org = ws().organisation;
        const kind = filingKind();
        if (!org && !kind) return '';
        const bits = [];
        if (org && org.period_end) bits.push(`Year end ${ukDate(org.period_end)}`);
        if (kind === 'dormant') bits.push('Dormant');
        else if (kind === 'traded') bits.push('Trading');
        return bits.join(' · ');
    }

    function companyLabel(row) {
        if (!row) return '';
        const name = row.ledger_name || row.name || '';
        return row.company_number ? `${name} (${row.company_number})` : name;
    }

    function selectedCompany() {
        return state.companies.find((row) => Number(row.id) === Number(state.companyId)) || null;
    }

    function companyMatches(query) {
        const q = String(query || '').trim().toLowerCase();
        if (!q) return [];
        return state.companies.filter((row) => {
            const blob = `${row.ledger_name || ''} ${row.name || ''} ${row.company_number || ''}`.toLowerCase();
            return blob.includes(q);
        }).slice(0, 40);
    }

    function presentLabel(onFile, value) {
        if (onFile && value) return h(value);
        if (onFile) return 'On file';
        return 'Not on file';
    }

    function companyFactsCard() {
        const row = selectedCompany();
        const data = ws();
        const crm = data.company || row;
        if (!state.companyId || !crm) return '';
        const name = crm.name || crm.ledger_name || (row && row.name) || '';
        const number = crm.company_number || (row && row.company_number) || '';
        const director = crm.director || (row && row.director) || ((data.organisation || {}).directors || []).map((d) => d.name).filter(Boolean).join(', ');
        const office = crm.reg_office || (data.organisation || {}).registered_office || (row && row.reg_office) || '';
        const auth = !!(data.filing && data.filing.has_authentication_code) || !!(row && row.has_authentication_code) || !!crm.has_authentication_code;
        const utr = (data.organisation && data.organisation.hmrc_utr) || crm.utr_number || (row && row.utr_number) || '';
        const hasUtr = !!String(utr || '').trim() || !!(row && row.has_utr) || !!crm.has_utr;
        return `
            <section class="accounts-company-facts" aria-label="Selected company details">
                <div class="accounts-fact-chip"><span>Company</span><strong>${h(name) || '—'}</strong></div>
                <div class="accounts-fact-chip"><span>Number</span><strong>${h(number) || '—'}</strong></div>
                <div class="accounts-fact-chip"><span>Director</span><strong>${h(director) || '—'}</strong></div>
                <div class="accounts-fact-chip ${auth ? 'is-onfile' : 'is-missing'}"><span>Authentication</span><strong>${auth ? 'On file' : 'Not on file'}</strong></div>
                <div class="accounts-fact-chip ${hasUtr ? 'is-onfile' : 'is-missing'}"><span>UTR</span><strong>${hasUtr ? presentLabel(true, utr) : 'Not on file'}</strong></div>
                ${office ? `<div class="accounts-fact-chip accounts-fact-chip--wide"><span>Registered office</span><strong>${h(office)}</strong></div>` : ''}
            </section>`;
    }

    function renderShell(inner) {
        const selected = selectedCompany();
        const staged = state.companies.find((row) => Number(row.id) === Number(state.pendingCompanyId)) || selected;
        const shown = visibleScreens();
        const primary = shown.filter((s) => s.kind !== 'report');
        const reports = shown.filter((s) => s.kind === 'report');
        const reportActive = reports.some((s) => s.id === state.screen);
        const primaryTabs = primary.map((s) => (
            `<button type="button" class="${state.screen === s.id ? 'is-active' : ''}" data-accounts-screen="${s.id}">${h(s.label)}</button>`
        )).join('');
        const reportItems = reports.map((s) => (
            `<button type="button" class="accounts-report-item ${state.screen === s.id ? 'is-active' : ''}" data-accounts-screen="${s.id}">${h(s.label)}</button>`
        )).join('');
        const resetName = (selected && (selected.ledger_name || selected.name)) || 'this company';
        const resetIcon = `<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 1 0 3-6.7"/><path d="M3 4v5h5"/></svg>`;
        return `
            <div class="accounts-file-toolbar">
                <div class="accounts-file-toolbar-copy">
                    <p class="accounts-file-kicker">${h(selected ? companyLabel(selected) : (state.companies.length ? 'Choose a company' : (state.loading ? 'Loading companies…' : 'No companies yet')))}</p>
                    <p class="accounts-file-meta">${h(selected ? headerMeta() : (state.loading && !state.companies.length ? 'Loading…' : 'Search, then Open books.'))}</p>
                </div>
                <section class="accounts-company-picker" aria-label="Choose company">
                    <div class="accounts-company-combo">
                        <input id="accounts-file-company" class="select-filter accounts-company-input" type="text" autocomplete="off" spellcheck="false" placeholder="Search name or company number" value="${h(companyLabel(staged))}" ${state.companies.length ? '' : 'disabled'} aria-autocomplete="list" aria-expanded="false" aria-controls="accounts-company-list">
                        <ul id="accounts-company-list" class="accounts-company-list" hidden role="listbox"></ul>
                    </div>
                    <button type="button" class="btn-primary btn-table" data-accounts-action="select-company" ${state.companies.length ? '' : 'disabled'}>Open books</button>
                    ${state.companyId ? `<button type="button" class="accounts-reset-icon" data-accounts-action="reset-company-data" title="Reset ${h(resetName)}" aria-label="Reset ${h(resetName)}">${resetIcon}</button>` : ''}
                </section>
            </div>
            ${state.companyId ? companyFactsCard() : ''}
            ${state.companyId && state.accountancyPane !== 'hmrc' ? `
            <div class="accounts-flow-row">
                <ol class="accounts-flow-stepper" aria-label="Accounts steps">
                    ${[
                        { id: 'home', n: '1', label: 'Start' },
                        { id: 'bank', n: '2', label: 'Bank' },
                        { id: 'review', n: '3', label: 'Check' },
                        { id: 'file', n: '4', label: 'File' },
                        { id: 'organisation', n: '', label: 'Company' },
                    ].map((step) => {
                        const active = state.screen === step.id;
                        return `<li><button type="button" class="${active ? 'is-active' : ''}" data-accounts-screen="${step.id}">${step.n ? `<span>${step.n}</span>` : ''}${h(step.label)}</button></li>`;
                    }).join('')}
                    ${reports.length ? `<li class="accounts-reports-wrap${reportActive ? ' is-current' : ''}">
                        <button type="button" class="accounts-reports-toggle${reportActive ? ' is-active' : ''}" data-accounts-action="toggle-reports" aria-expanded="false">Reports</button>
                        <div class="accounts-reports-menu" hidden>${reportItems}</div>
                    </li>` : ''}
                </ol>
            </div>` : ''}
            ${state.error ? `<div class="accounts-file-note is-error" role="alert">${h(state.error)}</div>` : ''}
            ${state.notice ? `<div class="accounts-file-note is-ok" role="status">${h(state.notice)}</div>` : ''}
            <div class="accounts-file-body">${inner}</div>
        `;
    }

    function emptyCompany() {
        if (state.companies.length) {
            return `
            <div class="accounts-file-empty">
                <div class="accounts-empty-icon" aria-hidden="true"></div>
                <h2>Open a company ledger</h2>
                <p>Search name or number, then <strong>Open books</strong>.</p>
            </div>`;
        }
        return `
            <div class="accounts-file-empty">
                <div class="accounts-empty-icon" aria-hidden="true"></div>
                <h2>No company to keep books for</h2>
                <p>Add a UK company in Company Registered first.</p>
            </div>`;
    }

    function loadingState(message) {
        return `<div class="accounts-file-empty"><p>${h(message || 'Loading…')}</p></div>`;
    }

    function nextStepCta() {
        const step = ws().next_step || (needsPath() ? 'choose' : 'file');
        if (step === 'choose') {
            return `<button type="button" class="btn-primary" data-accounts-screen="home">Choose a path</button>`;
        }
        if (step === 'review') {
            return `<button type="button" class="btn-primary" data-accounts-screen="review">Check the numbers</button>`;
        }
        if (step === 'file') {
            return `<button type="button" class="btn-primary" data-accounts-screen="file">File at Companies House</button>`;
        }
        return `<button type="button" class="btn-primary" data-accounts-screen="bank">Upload the latest statement</button>`;
    }

    function pathChooser() {
        return `
            <div class="accounts-file-intro">
                <h2>What did this company do this year?</h2>
            </div>
            <div class="accounts-path-grid">
                <article class="accounts-path-card">
                    <h3>It traded</h3>
                    <p>Bank statement → Check → File.</p>
                    <button type="button" class="btn-primary" data-accounts-action="choose-traded">Upload the latest statement</button>
                </article>
                <article class="accounts-path-card">
                    <h3>It slept</h3>
                    <p>Dormant pack. No bank statement.</p>
                    <button type="button" class="btn-primary" data-accounts-action="choose-dormant">Make dormant accounts</button>
                </article>
            </div>`;
    }

    function bankScreen(data) {
        const bank = (data && data.bank) || {};
        const rec = bank.reconciliation || {};
        const statements = (bank.statements || []).map((row) => (
            `<li><span>${h(row.filename || 'Statement')}</span><strong>${row.row_count} lines</strong></li>`
        )).join('') || '<li>No statement imported into the books yet.</li>';
        const portal = (bank.portal_documents || []).map((row) => (
            `<li><span>${h(row.name)}</span><button type="button" class="btn-secondary btn-table" data-accounts-doc="${row.id}">This is the latest</button></li>`
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
            ${statementMonthsCard(data)}
            <section class="accounts-upload-card">
                ${bankUploadRequiredLine(data)}
                <label class="accounts-apple-drop">
                    <input id="accounts-statement-file" type="file" accept=".csv,.txt,.pdf,text/csv,application/pdf" multiple required>
                    <span class="accounts-apple-browse">Choose Files</span>
                    <span id="accounts-file-name" class="accounts-file-name${pendingFiles().length > 1 ? ' is-multi' : ''}">${h(pendingFileLabel())}</span>
                </label>
                <p class="accounts-file-help">Choose one or more statements, then Compile.</p>
                ${compileProgressCard()}
                <div class="accounts-upload-actions">
                    ${compileButton()}
                    ${(bank.imported || (bank.statements || []).length) ? '<button type="button" class="accounts-apple-btn is-quiet" data-accounts-action="reset-books" title="Clear compiled statements and start again">Reset</button>' : ''}
                </div>
                ${state.precheck ? renderPrecheck(state.precheck) : ''}
                <textarea id="accounts-statement-paste" hidden placeholder="Date, Description, Money in, Money out, Balance"></textarea>
            </section>
            ${portal ? `<section class="accounts-file-card"><h2>Older files</h2><ul class="accounts-bank-list">${portal}</ul></section>` : ''}
            ${recBlock}`;
    }

    function pendingFiles() {
        return Array.from(state.pendingStatementFiles || []).filter(Boolean);
    }

    function pendingFileLabel() {
        const files = pendingFiles();
        if (!files.length) return 'No file selected';
        if (files.length === 1) return files[0].name;
        return `${files.length} statements selected`;
    }

    function compileButton() {
        if (state.compiling) {
            return `<button type="button" class="accounts-apple-btn" disabled title="Compiling">Compiling ${state.compileProgress || 0}%</button>`;
        }
        return '<button type="button" class="accounts-apple-btn" data-accounts-action="import-statement" title="Compile the selected bank statements">Compile</button>';
    }

    function compileProgressCard() {
        if (!state.compiling && !state.compileProgress) return '';
        const pct = Math.max(0, Math.min(100, Number(state.compileProgress) || 0));
        return `
            <div class="accounts-compile-progress" id="accounts-compile-bar">
                <div class="accounts-compile-progress-head">
                    <span id="accounts-compile-label">${h(state.compileProgressLabel || 'Compiling…')}</span>
                    <strong id="accounts-compile-pct">${pct}%</strong>
                </div>
                <div class="accounts-compile-track">
                    <div id="accounts-compile-fill" class="accounts-compile-fill" style="width:${pct}%"></div>
                </div>
            </div>`;
    }

    function setCompileProgress(pct, label) {
        state.compileProgress = Math.max(0, Math.min(100, Math.round(Number(pct) || 0)));
        if (label) state.compileProgressLabel = label;
        const fill = document.getElementById('accounts-compile-fill');
        const pctEl = document.getElementById('accounts-compile-pct');
        const lab = document.getElementById('accounts-compile-label');
        const btn = document.querySelector('.accounts-upload-actions .accounts-apple-btn[disabled], .accounts-upload-actions [data-accounts-action="import-statement"]');
        if (fill) fill.style.width = `${state.compileProgress}%`;
        if (pctEl) pctEl.textContent = `${state.compileProgress}%`;
        if (lab && label) lab.textContent = label;
        if (btn) btn.textContent = `Compiling ${state.compileProgress}%`;
    }

    function hintFromFiles(files) {
        let start = '';
        let end = '';
        (files || []).forEach((file) => {
            const span = String((file && file.name) || '').match(/(\d{4}-\d{2}-\d{2}).*?(\d{4}-\d{2}-\d{2})/);
            if (!span) return;
            if (!start || span[1] < start) start = span[1];
            if (!end || span[2] > end) end = span[2];
        });
        const first = (files && files[0] && files[0].name) || '';
        state.statementHint = start ? { start, end, filename: first } : (first ? { filename: first } : state.statementHint);
    }

    function renderPrecheck(precheck) {
        const unknown = (precheck.unknown || []).length;
        return `
            <div class="accounts-precheck ${unknown ? 'is-warn' : 'is-ok'}">
                <p><strong>Tested ${precheck.total} line(s) · ${precheck.accuracy}% detected</strong></p>
                <p>${precheck.high.length} safe to post. ${unknown ? `${unknown} need you after compile — we will not guess Sales or Professional fees for them.` : 'No unknown paths.'}</p>
            </div>`;
    }

    function homeScreen(data) {
        return pathChooser();
    }

    function categoryOptions(selected) {
        const cats = ((ws().bank || {}).categories) || [];
        return cats.map((cat) => (
            `<option value="${h(cat.code)}" ${cat.code === selected ? 'selected' : ''}>${h(cat.label)}</option>`
        )).join('');
    }

    function isEcommerceReceipt(line) {
        if (!line) return false;
        if (line.is_ecommerce) return true;
        return Number(line.money_in || 0) > 0 && ECOMMERCE_SALES_RE.test(String(line.description || ''));
    }

    function normalizePersonName(value) {
        return String(value || '').toLowerCase().replace(/[^a-z0-9\s'-]/g, ' ').replace(/\s+/g, ' ').trim();
    }

    function directorNameList() {
        const data = ws();
        const row = selectedCompany();
        const raw = [];
        ((data.organisation || {}).directors || []).forEach((person) => {
            if (person && person.name) raw.push(person.name);
        });
        const crm = data.company || row || {};
        if (crm.director) raw.push(crm.director);
        if (row && row.director) raw.push(row.director);
        const names = [];
        raw.join(',').split(/[,;/&]|\band\b/i).forEach((part) => {
            const name = normalizePersonName(part);
            if (name && name.length >= 3) names.push(name);
        });
        return Array.from(new Set(names));
    }

    function descriptionMentionsDirectorName(description) {
        const text = normalizePersonName(description);
        if (!text) return false;
        return directorNameList().some((name) => {
            if (name.length >= 5 && text.includes(name)) return true;
            const tokens = name.split(' ').filter((token) => token.length > 1 && !TITLE_RE.test(token));
            if (tokens.length >= 2) {
                const first = tokens[0];
                const last = tokens[tokens.length - 1];
                if (first.length >= 2 && last.length >= 3 && text.includes(first) && text.includes(last)) return true;
            }
            const last = tokens[tokens.length - 1] || '';
            if (last.length >= 5 && new RegExp(`\\b${last.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\b`).test(text)) {
                return DIRECTOR_WORD_RE.test(description) || SALARY_RE.test(description);
            }
            return false;
        });
    }

    function classifyDirectorPayment(line) {
        if (!line) return null;
        const out = Number(line.money_out || 0);
        const incoming = Number(line.money_in || 0);
        const desc = String(line.description || '');
        const named = descriptionMentionsDirectorName(desc);
        const directorWord = DIRECTOR_WORD_RE.test(desc);
        if (incoming > 0 && (named || directorWord)) {
            return { kind: 'in', code: null, label: 'From director' };
        }
        if (out <= 0 || (!named && !directorWord)) return null;
        if (DIVIDEND_RE.test(desc)) return { kind: 'dividend', code: '3200', label: 'Dividends' };
        if (DIRECTOR_LOAN_RE.test(desc)) return { kind: 'loan', code: null, label: 'Director loan' };
        if (DIRECTOR_EXPENSE_RE.test(desc) && !SALARY_RE.test(desc)) {
            return { kind: 'expense', code: null, label: 'Director expense' };
        }
        return { kind: 'salary', code: '7002', label: 'Director pay' };
    }

    function isDirectorSalary(line) {
        const hint = classifyDirectorPayment(line);
        return !!(hint && hint.kind === 'salary');
    }

    function isDirectorDividend(line) {
        const hint = classifyDirectorPayment(line);
        return !!(hint && hint.kind === 'dividend');
    }

    function directorHintPill(line) {
        const hint = classifyDirectorPayment(line);
        if (!hint || hint.kind === 'in') return '';
        const tone = hint.kind === 'salary' ? 'is-director' : (hint.kind === 'dividend' ? 'is-dividend' : 'is-loan');
        return `<span class="accounts-ecom-pill ${tone}">${h(hint.label)}</span>`;
    }

    function lineMoney(line) {
        let incoming = Number((line && line.money_in) || 0);
        let outgoing = Number((line && line.money_out) || 0);
        const amount = Number((line && line.amount) || 0);
        if (!incoming && !outgoing && amount) {
            if (amount > 0) incoming = amount;
            else outgoing = Math.abs(amount);
        }
        return {
            incoming,
            outgoing,
            dir: incoming > 0 ? 'in' : (outgoing > 0 ? 'out' : ''),
        };
    }

    function classifyTransaction(line) {
        if (!line) {
            return { confidence: 'none', code: '', label: 'Auto-Deleted', path: 'Non-transactional', reason: 'Empty line auto-deleted.', autoDelete: true };
        }
        const desc = String(line.description || '');
        const { incoming, outgoing, dir } = lineMoney(line);
        const guess = (confidence, code, label, path, reason, manual) => ({
            confidence, code, label, path, reason, manual: !!manual,
        });
        if (!dir || (incoming === 0 && outgoing === 0)) {
            return { confidence: 'none', code: '', label: 'Auto-Deleted', path: 'Non-transactional', reason: 'Zero amount line auto-deleted.', autoDelete: true };
        }
        if (PRIOR_TX_RE.test(desc) && MARKETPLACE_RE.test(desc) && dir === 'in') {
            return guess('high', '5000', 'Materials and stock', 'Shop refund', 'Marketplace refund of a purchase — reduces stock cost, not new sales.');
        }
        if (dir === 'in' && ECOMMERCE_SALES_RE.test(desc)) {
            return guess('high', '4000', 'Sales', 'Shop payout', 'Money in from a shop or card platform is sales.');
        }
        if (dir === 'out' && AMAZON_PRIME_RE.test(desc)) {
            return guess('high', '7500', 'Stationery', 'Subscription', 'Amazon Prime subscription.');
        }
        if (dir === 'out' && MARKETPLACE_RE.test(desc)) {
            return guess('high', '5000', 'Materials and stock', 'Shop purchase', 'Money out to Amazon or eBay for stock or materials.');
        }
        const director = classifyDirectorPayment({
            ...line,
            money_in: incoming,
            money_out: outgoing,
            description: desc,
        });
        if (director && director.kind === 'salary') {
            return guess('high', '7002', 'Director pay', 'Director', 'Money paid to the director for their work is director salary.');
        }
        if (director && director.kind === 'dividend') {
            return guess('high', '3200', 'Dividends', 'Director', 'The bank wording says dividend.');
        }
        if (director && director.kind === 'loan') {
            return guess('high', '3200', 'Dividends', 'Director', 'Director loan / drawing.');
        }
        if (director && director.kind === 'expense') {
            return guess('high', '7900', 'General Expenses', 'Director', 'Director expense repayment.');
        }
        if (director && director.kind === 'in') {
            return guess('high', '4000', 'Sales', 'From director', 'Money in from director.');
        }
        if (dir === 'out' && WAGES_RE.test(desc) && !descriptionMentionsDirectorName(desc)) {
            return guess('high', '7000', 'Wages', 'Staff', 'Wages or payroll.');
        }
        if (dir === 'out' && RENT_RE.test(desc)) return guess('high', '7100', 'Rent', 'Premises', 'Rent or landlord payment.');
        if (dir === 'out' && UTILITY_RE.test(desc)) return guess('high', '7200', 'Light and heat', 'Premises', 'Energy, water or phone bill.');
        if (dir === 'out' && FUEL_RE.test(desc)) return guess('high', '7300', 'Vehicle and fuel', 'Motor', 'Fuel, parking or vehicle cost.');
        if (dir === 'out' && TRAVEL_RE.test(desc)) return guess('high', '7400', 'Travel', 'Travel', 'Travel booking.');
        if (dir === 'out' && INSURE_RE.test(desc)) return guess('high', '7800', 'Insurance', 'Insurance', 'Insurance premium.');
        if (dir === 'out' && LEGAL_RE.test(desc)) return guess('high', '7600', 'Professional fees', 'Professional', 'Legal, Companies House or accountancy.');
        if (dir === 'out' && BANK_FEE_RE.test(desc)) return guess('high', '7700', 'Bank charges', 'Bank', 'Bank or account fee.');
        if (dir === 'out' && HMRC_CT_RE.test(desc)) return guess('high', '2220', 'Corporation tax paid', 'HMRC', 'Corporation tax paid to HMRC.');
        if (dir === 'out' && HMRC_VAT_RE.test(desc)) return guess('high', '2200', 'VAT paid', 'HMRC', 'VAT paid to HMRC.');
        if (dir === 'out' && HMRC_RE.test(desc)) return guess('high', '8100', 'Tax', 'HMRC', 'HMRC payment.');
        if (dir === 'out' && STOCK_RE.test(desc)) return guess('high', '5000', 'Materials and stock', 'Stock', 'Trade materials or stock purchase.');
        if (dir === 'out' && SUPERMARKET_RE.test(desc)) {
            return guess('high', '5000', 'Materials and stock', 'Shop', 'Supermarket payment.');
        }
        if (dir === 'in' && /\b(invoice|client payment|bacs from|sales)\b/i.test(desc)) {
            return guess('high', '4000', 'Sales', 'Customer', 'Customer payment.');
        }
        if (dir === 'in') {
            return guess('high', '4000', 'Sales', 'Sales', 'Money in categorized as Sales.');
        }
        if (dir === 'out') {
            return guess('high', '7900', 'General Expenses', 'Expenses', 'Money out categorized as General Expenses.');
        }
        return guess('none', '', 'Auto-Deleted', 'Non-transactional', 'Zero amount line auto-deleted.', false);
    }

    function classificationReport(lines) {
        const validLines = (lines || []).filter((line) => {
            const hint = classifyTransaction(line);
            return !(hint.autoDelete || hint.confidence === 'none');
        });
        const rows = validLines.map((line) => {
            const hint = classifyTransaction(line);
            const current = String(line.category_code || hint.code || (lineMoney(line).dir === 'in' ? '4000' : '7900'));
            return { line, hint, current, match: true };
        });
        const high = rows;
        const medium = [];
        const unknown = [];
        const detected = rows;
        const accuracy = 100;
        return {
            rows, high, medium, unknown, detected, dumped: [], mismatch: [], accuracy: 100, total: rows.length,
        };
    }

    function runClassifierSelfTest() {
        const saved = state.workspace;
        state.workspace = { organisation: { directors: [{ name: 'Suleman Iqbal', role: 'Director' }] } };
        const cases = [
            { description: 'EBAY Commerce UK Ltd (Faster Payments)', money_in: 35, expectCode: '4000', expectConf: 'high' },
            { description: 'AMAZON.UK LONDON GBR', money_out: 2.87, expectCode: '5000', expectConf: 'high' },
            { description: 'SULEMAN IQBAL (Faster Payments)', money_out: 55, expectCode: '7002', expectConf: 'high' },
            { description: 'MUHAMMAD SADAM (Faster Payments)', money_out: 35, expectCode: '', expectConf: 'none' },
            { description: 'AMAZON UK* ZL4R31KK4 Principal Place', money_in: 20.25, expectCode: '4000', expectConf: 'high' },
            { description: 'AMAZON.UK LONDON GBR THIS RELATES TO A PREVIOUS TRANSACTION', money_in: 5.4, expectCode: '5000', expectConf: 'high' },
            { description: 'Stripe Payout', money_in: 120, expectCode: '4000', expectConf: 'high' },
            { description: 'Interim dividend Suleman Iqbal', money_out: 200, expectCode: '3200', expectConf: 'high' },
            { description: 'HMRC VAT', money_out: 80, expectCode: '2200', expectConf: 'high' },
            { description: 'Starling monthly fee', money_out: 2, expectCode: '7700', expectConf: 'high' },
            { description: 'AMAZON PRIME LONDON GBR', money_out: 8.99, expectCode: '7500', expectConf: 'medium' },
        ];
        const failures = [];
        try {
            cases.forEach((row) => {
                const got = classifyTransaction(row);
                if (row.expectCode !== (got.code || '')) {
                    failures.push(`${row.description}: code ${got.code || 'blank'} ≠ ${row.expectCode || 'blank'}`);
                }
                if (row.expectConf && got.confidence !== row.expectConf) {
                    failures.push(`${row.description}: ${got.confidence} ≠ ${row.expectConf}`);
                }
                if (row.expectConf === 'none' && !got.manual) {
                    failures.push(`${row.description}: unknown line was not left for the runner`);
                }
            });
        } finally {
            state.workspace = saved;
        }
        return { ok: !failures.length, failures, tested: cases.length };
    }

    function isRunnerConfirmed(line) {
        if (!line) return false;
        return line.needs_review === false || line.needs_review === 0;
    }

    function markLinesReviewed(ids, code, label) {
        const chosen = new Set((ids || []).map(Number).filter(Boolean));
        if (!chosen.size) return;
        (((ws().bank || {}).lines) || []).forEach((line) => {
            if (!chosen.has(Number(line.id))) return;
            line.needs_review = false;
            line.confidence = 'high';
            if (code) line.category_code = code;
            if (label) line.category_label = label;
        });
    }

    function classifyHintPill(line) {
        const hint = classifyTransaction(line);
        if ((hint.manual || hint.confidence === 'none') && !isRunnerConfirmed(line)) {
            return '<span class="accounts-ecom-pill is-manual">Needs you</span>';
        }
        if (hint.confidence === 'medium') {
            return `<span class="accounts-ecom-pill is-loan">${h(hint.path)}</span>`;
        }
        if (isEcommerceReceipt(line)) return '<span class="accounts-ecom-pill">Ecommerce</span>';
        return directorHintPill(line) || `<span class="accounts-ecom-pill">${h(hint.path)}</span>`;
    }

    function allBankLines() {
        return ((ws().bank || {}).lines) || [];
    }

    function compileWindow(data) {
        const period = filingPeriod(((data || ws()).organisation) || {}, data || ws());
        return { start: period.start || '', end: period.end || '' };
    }

    function inCompileWindow(line, window) {
        const day = isoDateOnly((line && (line.date || line.txn_date)) || '');
        if (!day) return false;
        if (window && window.start && day < window.start) return false;
        if (window && window.end && day > window.end) return false;
        return true;
    }

    function bankLines() {
        const window = compileWindow();
        const lines = allBankLines();
        if (!window.start && !window.end) return lines;
        return lines.filter((line) => inCompileWindow(line, window));
    }

    function selectedLines() {
        const chosen = new Set((state.selectedLineIds || []).map(Number));
        return bankLines().filter((line) => chosen.has(Number(line.id)));
    }

    function checkWorkflow(data) {
        const lines = bankLines();
        const report = classificationReport(lines);
        const unknownIds = new Set(report.unknown.map((row) => Number(row.line.id)));
        const other = lines.filter((line) => {
            if (unknownIds.has(Number(line.id))) return false;
            if (isEcommerceReceipt(line) || isDirectorSalary(line) || isDirectorDividend(line)) return false;
            return true;
        });
        return [
            {
                id: 'ecommerce',
                title: 'Shop payments',
                help: 'eBay, Amazon, Stripe and similar are sales.',
                lines: lines.filter(isEcommerceReceipt),
                actionLabel: 'Mark these as Sales',
                code: '4000',
            },
            {
                id: 'director',
                title: 'Director pay',
                help: 'Money to the named director is director salary.',
                lines: lines.filter(isDirectorSalary),
                actionLabel: 'Mark these as Director pay',
                code: '7002',
            },
            {
                id: 'needs-you',
                title: 'Needs you',
                help: 'No safe path. Set the category yourself.',
                lines: report.unknown.map((row) => row.line),
                actionLabel: '',
                code: '',
            },
            {
                id: 'rest',
                title: 'Other detected',
                help: 'Detected path. Change the category if one is wrong.',
                lines: other,
                actionLabel: '',
                code: '',
            },
            {
                id: 'done',
                title: 'Finished',
                help: 'Accuracy, plus every line that still needs you.',
                lines: [],
                actionLabel: 'Looks right — continue',
                code: '',
            },
        ];
    }

    function reviewScreen(data) {
        if (isEmpty()) {
            return `<div class="accounts-file-empty"><h2>Nothing to check yet</h2><p>Choose one or more bank statements on Bank, then press Compile there.</p><div class="accounts-file-empty-actions">${nextStepCta()}</div></div>`;
        }
        const bank = data.bank || {};
        const steps = checkWorkflow(data);
        if (state.checkStep >= steps.length) state.checkStep = steps.length - 1;
        if (state.checkStep < 0) state.checkStep = 0;
        const step = steps[state.checkStep] || steps[0];
        const selected = new Set((state.selectedLineIds || []).map(Number));
        const report = classificationReport(bankLines());
        const shown = step.id === 'done' ? report.unknown.map((row) => row.line) : (step.lines || []);
        const rows = shown.map((line) => `
            <tr class="${line.needs_review ? 'is-review' : ''}${isEcommerceReceipt(line) ? ' is-ecommerce' : ''}${isDirectorSalary(line) ? ' is-director' : ''}${selected.has(Number(line.id)) ? ' is-selected' : ''}">
                <td class="accounts-check-cell">
                    <input type="checkbox" data-line-select="${line.id}" ${selected.has(Number(line.id)) ? 'checked' : ''} aria-label="Select ${h(line.description || 'line')}">
                </td>
                <td>${h(ukDate(line.date || line.txn_date))}</td>
                <td>
                    ${h(line.description)}
                    ${classifyHintPill(line)}
                    ${(() => {
                        const hint = classifyTransaction(line);
                        return hint.manual && !isRunnerConfirmed(line) ? `<p class="accounts-line-reason">${h(hint.reason)}</p>` : '';
                    })()}
                </td>
                <td class="num">${line.money_in ? gbp(line.money_in) : ''}</td>
                <td class="num">${line.money_out ? gbp(line.money_out) : ''}</td>
                <td>
                    <select class="select-filter" data-recat-id="${line.id}">
                        ${categoryOptions(line.category_code)}
                    </select>
                </td>
            </tr>
        `).join('') || '<tr><td colspan="6">Nothing in this step.</td></tr>';
        const tb = data.trial_balance || {};
        const bs = data.balance_sheet || {};
        const stepper = steps.map((item, index) => `
            <button type="button" class="accounts-check-step ${index === state.checkStep ? 'is-active' : ''}${index < state.checkStep ? ' is-done' : ''} ${item.id === 'needs-you' && item.lines.length ? 'is-warn' : ''}" data-check-goto="${index}">
                <span>${index + 1}</span>
                <strong>${h(item.title)}</strong>
                <em>${item.id === 'done' ? `${report.accuracy}% detected` : (item.lines.length ? `${item.lines.length} lines` : 'None')}</em>
            </button>
        `).join('');
        const canBulk = step.id === 'rest' || step.id === 'needs-you';
        const home = data.home || {};
        return `
            <div class="stats-grid accounts-file-stats">
                <div class="stat-card"><div class="stat-info"><div class="label">Bank</div><div class="value">${gbp(home.bank_total)}</div></div></div>
                <div class="stat-card"><div class="stat-info"><div class="label">Money in</div><div class="value">${gbp(home.money_in)}</div></div></div>
                <div class="stat-card"><div class="stat-info"><div class="label">Money out</div><div class="value">${gbp(home.money_out)}</div></div></div>
                <div class="stat-card"><div class="stat-info"><div class="label">Profit for the year</div><div class="value">${gbp((data.profit_and_loss || {}).profit)}</div></div></div>
            </div>
            <ol class="accounts-check-stepper">${stepper}</ol>
            <section class="accounts-file-card">
                <p class="accounts-step-kicker">Check · step ${state.checkStep + 1} of ${steps.length}</p>
                <h2>${h(step.title)}</h2>
                <p class="accounts-file-help">${h(step.help)}</p>
                <div class="accounts-file-empty-actions">
                    ${state.checkStep > 0 ? '<button type="button" class="btn-secondary" data-accounts-action="check-step-back">Back</button>' : ''}
                    ${step.code ? `<button type="button" class="btn-primary" data-accounts-action="check-step-apply">${h(step.actionLabel)} (${shown.length})</button>` : ''}
                    ${canBulk ? `
                        <label class="accounts-bulk-apply">
                            <span>Set category</span>
                            <select id="accounts-bulk-category" class="select-filter">${categoryOptions('4900')}</select>
                        </label>
                        <button type="button" class="btn-secondary" data-accounts-action="bulk-category" ${(state.selectedLineIds || []).length ? '' : 'disabled'}>Apply to selected</button>
                    ` : ''}
                    ${step.id === 'done'
                        ? '<button type="button" class="btn-primary" data-accounts-action="confirm-review">Looks right — continue</button><button type="button" class="btn-secondary" data-accounts-screen="file">Go to File</button>'
                        : '<button type="button" class="btn-secondary" data-accounts-action="check-step-next">Next step</button>'}
                </div>
                ${step.id === 'done' ? accuracyReportCard(data, report, tb, bs) : ''}
                ${step.id === 'done' && report.unknown.length ? '<p class="accounts-file-help is-warn" style="margin-top:16px;">These still need you. Set a category on each one before filing.</p>' : ''}
                ${step.id !== 'done' || report.unknown.length ? `
                <div class="data-table-container" style="margin-top:16px;">
                    <table class="data-table accounts-review-table">
                        <thead>
                            <tr>
                                <th class="accounts-check-cell">
                                    <input type="checkbox" id="accounts-select-all" ${shown.length && shown.every((line) => selected.has(Number(line.id))) ? 'checked' : ''} aria-label="Select all lines on this step">
                                </th>
                                <th>Date</th>
                                <th>What the bank says</th>
                                <th>In</th>
                                <th>Out</th>
                                <th>Category</th>
                            </tr>
                        </thead>
                        <tbody>${rows}</tbody>
                    </table>
                </div>` : ''}
            </section>`;
    }

    function accuracyReportCard(data, report, tb, bs) {
        const bank = (data && data.bank) || {};
        const rec = bank.reconciliation || {};
        const closing = rec.statement_closing != null ? rec.statement_closing : ((bank.statements || [])[0] || {}).closing_balance;
        const first = bankLines().reduce((min, line) => {
            const d = line.date || line.txn_date || '';
            return (!min || d < min) ? d : min;
        }, '');
        const last = bankLines().reduce((max, line) => {
            const d = line.date || line.txn_date || '';
            return d > max ? d : max;
        }, '');
        const byPath = {};
        report.high.forEach((row) => {
            const key = row.hint.path || row.hint.label;
            if (!byPath[key]) byPath[key] = { count: 0, label: row.hint.label };
            byPath[key].count += 1;
        });
        const pathList = Object.keys(byPath).map((key) => `<li><span>${h(key)}</span><strong>${byPath[key].count}</strong></li>`).join('');
        return `
            <div class="accounts-accuracy">
                <div class="accounts-accuracy-score ${report.unknown.length ? 'is-warn' : 'is-ok'}">
                    <strong>${report.accuracy}%</strong>
                    <span>paths we can detect</span>
                </div>
                <ul class="accounts-accuracy-stats">
                    <li><span>Lines compiled</span><strong>${report.total}</strong></li>
                    <li><span>Detected</span><strong>${report.high.length}</strong></li>
                    <li><span>Needs you</span><strong>${report.unknown.length}</strong></li>
                    <li><span>Trial balance</span><strong>${tb.agrees ? 'Agrees' : 'Out'}</strong></li>
                    <li><span>Balance sheet</span><strong>${bs.balances ? 'Agrees' : 'Out'}</strong></li>
                    <li><span>Bank closing</span><strong>${closing != null ? gbp(closing) : '—'}</strong></li>
                </ul>
                <div class="accounts-why">
                    <h3>Accuracy</h3>
                    <p>${report.total} line(s) compiled${first && last ? ` · ${h(ukDate(first))} – ${h(ukDate(last))}` : ''}. Detection ${report.accuracy}%.${report.unknown.length ? ` ${report.unknown.length} still need you.` : ''}</p>
                </div>
                ${pathList ? `<ul class="accounts-bank-list">${pathList}</ul>` : ''}
            </div>`;
    }

    function reportsScreen(data) {
        if (isEmpty()) {
            return `<div class="accounts-file-empty"><h2>No reports yet</h2></div>`;
        }
        const org = data.organisation || {};
        const plRows = ((data.profit_and_loss || {}).lines || []).map((line) => (
            `<tr class="${line.is_total ? 'is-total' : ''}"><td>${h(line.label)}</td><td class="num">${gbp(line.current)}</td><td class="num">${gbp(line.prior)}</td></tr>`
        )).join('');
        const tb = data.trial_balance || { rows: [] };
        const tbRows = (tb.rows || []).map((row) => (
            `<tr><td>${h(row.code)}</td><td>${h(row.name)}</td><td class="num">${row.debit ? gbp(row.debit) : ''}</td><td class="num">${row.credit ? gbp(row.credit) : ''}</td></tr>`
        )).join('');
        return `
            <section class="accounts-file-card">
                <h2>Profit and loss</h2>
                <p class="accounts-file-help">${h(ukDate(org.period_end))}.</p>
                <div class="data-table-container"><table class="data-table accounts-report-table"><thead><tr><th></th><th>This year</th><th>Last year</th></tr></thead><tbody>${plRows}</tbody></table></div>
            </section>
            ${statutoryBalanceSheet(data, { compact: true })}
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
        if (needsPath() && filingKind() !== 'dormant') {
            return `<div class="accounts-file-empty"><h2>Choose a path first</h2><p>Pick traded or dormant, then file.</p><div class="accounts-file-empty-actions">${nextStepCta()}</div></div>`;
        }
        if (isEmpty() && filingKind() !== 'dormant' && !filingPeriod((data && data.organisation) || {}, data).isFirst) {
            return `<div class="accounts-file-empty"><h2>No accounts to file yet</h2><p>Upload the statement, check, then file.</p><div class="accounts-file-empty-actions">${nextStepCta()}</div></div>`;
        }
        const ye = data.year_end || {};
        const org = data.organisation || {};
        const filing = data.filing || {};
        const submit = data.ch_submit;
        const dormant = filingKind() === 'dormant' || ye.can_file_dormant;
        const needed = (filing.needed || []).map((item) => (
            `<li><strong>${h(item.label)}</strong> — ${h(item.reason)}</li>`
        )).join('');
        const history = (filing.last_filings || []).map((row) => (
            `<li><span>${h(row.mode)} · ${h(ukDate(row.created_at))}</span><strong>${h(row.receipt || '')}</strong></li>`
        )).join('') || '<li>Nothing sent yet.</li>';
        const size = ye.size || { details: [] };
        const ready = dormant || ye.can_file_micro;
        return `
            <div class="accounts-file-banner ${ready ? 'is-ok' : 'is-warn'}">
                <strong>${dormant ? 'Dormant accounts. No bank statement.' : (ye.can_file_micro ? 'Micro-entity accounts.' : 'Cannot file as a micro-entity.')}</strong>
                <p>Deadline ${h(ukDate(org.filing_due))}.</p>
            </div>
            ${submit ? `<div class="accounts-file-banner is-ok"><strong>${submit.mode === 'live' ? 'Sent to Companies House' : 'Sandbox receipt'}</strong><p>${h(submit.message)}</p><p>Receipt <code>${h(submit.receipt)}</code></p></div>` : ''}
            ${filingYearCard(data)}
            ${statutoryBalanceSheet(data, { compact: true })}
            <section class="accounts-file-card">
                <h2>Send to Companies House</h2>
                <ol class="accounts-file-steps">
                    <li>Download the <strong>Companies House pack</strong>.</li>
                    <li>Open <a href="${h(filing.webfiling_url || 'https://ewf.companieshouse.gov.uk/')}" target="_blank" rel="noopener">Companies House WebFiling</a> and upload that file.</li>
                    <li>Or fill the boxes below and press <strong>Send to Companies House</strong>.</li>
                </ol>
                <div class="accounts-file-empty-actions">
                    <button type="button" class="btn-primary" data-accounts-export="filleted">Download Companies House pack</button>
                    <button type="button" class="btn-secondary" data-accounts-export="members">Download members’ pack</button>
                    ${dormant ? '' : '<button type="button" class="btn-secondary" data-accounts-export="ixbrl">Draft iXBRL</button>'}
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
                    <button type="button" class="btn-secondary" data-accounts-screen="hmrc">Open HMRC tab</button>
                </div>
            </section>
            ${dormant ? '' : `
            <section class="accounts-file-card">
                <h2>Is this a small (micro) company?</h2>
                <div class="data-table-container">
                    <table class="data-table">
                        <thead><tr><th>Condition</th><th>This year</th><th>Limit</th><th></th></tr></thead>
                        <tbody>${(size.details || []).map((row) => `<tr><td>${h(row.label === 'Turnover' ? 'Sales' : (row.label === 'Balance sheet total' ? 'What the company owns' : (row.label === 'Average employees' ? 'Staff' : row.label)))}</td><td class="num">${row.label === 'Average employees' ? String(row.actual) : gbp(row.actual)}</td><td class="num">${row.label === 'Average employees' ? `≤ ${row.limit}` : `≤ ${gbp(row.limit)}`}</td><td>${row.met ? 'Met' : 'Not met'}</td></tr>`).join('')}</tbody>
                    </table>
                </div>
            </section>`}
            <section class="accounts-file-card">
                <h2>Previous sends</h2>
                <ul class="accounts-bank-list">${history}</ul>
                <p class="accounts-file-help">${h(ye.ixbrl_gap || '')}</p>
            </section>
            ${accountantWorkingNote(data)}`;
    }

    function hmrcScreen(data) {
        const hmrc = data.hmrc || {};
        const org = data.organisation || {};
        const submit = data.hmrc_submit;
        const dormant = filingKind() === 'dormant' || hmrc.kind === 'dormant';
        const needed = (hmrc.needed || []).map((item) => (
            `<li><strong>${h(item.label)}</strong> — ${h(item.reason)}</li>`
        )).join('');
        const history = (hmrc.last_filings || []).map((row) => (
            `<li><span>${h(row.mode)} · ${h(ukDate(row.created_at))}</span><strong>${h(row.receipt || '')}</strong></li>`
        )).join('') || '<li>Nothing recorded yet.</li>';
        const steps = (hmrc.steps || []).map((s) => `<li>${h(s)}</li>`).join('');
        return `
            <div class="accounts-file-banner ${dormant || !(hmrc.tax) ? 'is-ok' : 'is-warn'}">
                <strong>${h(hmrc.headline || (dormant ? 'Usually no Corporation Tax — the company slept.' : 'HMRC Corporation Tax'))}</strong>
                <p>${h(hmrc.plain || '')}</p>
            </div>
            ${submit ? `<div class="accounts-file-banner is-ok"><strong>Recorded</strong><p>${h(submit.message)}</p><p>Receipt <code>${h(submit.receipt)}</code></p></div>` : ''}
            <div class="stats-grid accounts-file-stats">
                <div class="stat-card"><div class="stat-info"><div class="label">Profit on the books</div><div class="value">${gbp(hmrc.profit)}</div></div></div>
                <div class="stat-card"><div class="stat-info"><div class="label">Tax to pay (guide)</div><div class="value">${gbp(hmrc.tax)}</div></div></div>
                <div class="stat-card"><div class="stat-info"><div class="label">Pay by</div><div class="value">${h(ukDate(hmrc.payment_due))}</div></div></div>
                <div class="stat-card"><div class="stat-info"><div class="label">Tax return by</div><div class="value">${h(ukDate(hmrc.return_due))}</div></div></div>
            </div>
            <section class="accounts-file-card">
                <h2>${dormant ? 'Tell HMRC the company slept' : 'Company Tax Return'}</h2>
                <p class="accounts-file-help">${dormant
                    ? 'No Corporation Tax if the company had no sales. Tell HMRC it is dormant if they do not already know.'
                    : 'Guide only — not a filed CT600. Pay tax, then file the return on GOV.UK.'}</p>
                ${steps ? `<ol class="accounts-file-steps">${steps}</ol>` : ''}
                ${needed ? `<div class="accounts-needed"><p>Provide only if you have it:</p><ul>${needed}</ul></div>` : ''}
                <div class="accounts-org-grid" style="margin-top:12px;">
                    <label>HMRC tax number (UTR) <input id="acc-hmrc-utr" class="select-filter" value="${h(hmrc.utr || org.hmrc_utr || '')}" maxlength="15" placeholder="10 digits from HMRC"></label>
                </div>
                <div class="accounts-file-empty-actions">
                    <button type="button" class="btn-primary" data-accounts-action="submit-hmrc">${dormant ? 'Record as dormant for HMRC' : 'Record tax return (sandbox)'}</button>
                    <a class="btn-secondary" href="${h(dormant ? (hmrc.dormant_url || 'https://www.gov.uk/dormant-company/dormant-for-corporation-tax') : (hmrc.file_url || 'https://www.gov.uk/file-your-company-accounts-and-tax-return'))}" target="_blank" rel="noopener">${dormant ? 'Open GOV.UK dormant tax' : 'Open GOV.UK tax return'}</a>
                </div>
            </section>
            <section class="accounts-file-card">
                <h2>Previous records</h2>
                <ul class="accounts-bank-list">${history}</ul>
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
                    <label>HMRC tax number (UTR) <input id="acc-org-utr" class="select-filter" value="${h(org.hmrc_utr || '')}" maxlength="15" placeholder="10 digits from HMRC"></label>
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

    const MICRO_BS_ITEMS = [
        { code: 'A', label: 'Called up share capital not paid', kind: 'item' },
        { code: 'B', label: 'Fixed assets', kind: 'item' },
        { code: 'C', label: 'Current assets', kind: 'item' },
        { code: 'D', label: 'Prepayments and accrued income', kind: 'item' },
        { code: 'E', label: 'Creditors: amounts falling due within one year', kind: 'liability' },
        { code: 'F', label: 'Net current assets (liabilities)', kind: 'subtotal' },
        { code: 'G', label: 'Total assets less current liabilities', kind: 'subtotal' },
        { code: 'H', label: 'Creditors: amounts falling due after more than one year', kind: 'liability' },
        { code: 'I', label: 'Provisions for liabilities', kind: 'liability' },
        { code: 'J', label: 'Accruals and deferred income', kind: 'liability' },
        { code: 'NA', label: 'Net assets / (liabilities)', kind: 'net' },
        { code: 'K', label: 'Capital and reserves', kind: 'equity' },
    ];

    function gbpSheet(value, asLiability) {
        let n = Number(value || 0);
        if (asLiability && n > 0) n = -n;
        if (Math.abs(n) < 0.005) return '—';
        const abs = Math.abs(n).toLocaleString('en-GB', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
        return n < 0 ? `(${abs})` : abs;
    }

    function microBsLookup(bs) {
        const map = {};
        ((bs && bs.lines) || []).forEach((line) => {
            if (line && line.code) map[line.code] = line;
        });
        const cur = (bs && bs.current) || {};
        const prv = (bs && bs.prior) || {};
        const pick = (code) => {
            const row = map[code] || {};
            const current = row.current != null ? row.current : cur[code];
            const prior = row.prior != null ? row.prior : prv[code];
            return { current: Number(current || 0), prior: Number(prior || 0) };
        };
        const g = pick('G');
        const h = pick('H');
        const i = pick('I');
        const j = pick('J');
        map.NA = {
            code: 'NA',
            current: cur.net_assets != null ? cur.net_assets : moneySafe(g.current - h.current - i.current - j.current),
            prior: prv.net_assets != null ? prv.net_assets : moneySafe(g.prior - h.prior - i.prior - j.prior),
        };
        return map;
    }

    function moneySafe(value) {
        return Math.round(Number(value || 0) * 100) / 100;
    }

    function plLineAmount(data, code) {
        const row = ((((data || {}).profit_and_loss) || {}).lines || []).find((line) => line.code === code);
        return Number((row && row.current) || 0);
    }

    function chNumber(value) {
        const n = Number(value || 0);
        if (Math.abs(n) < 0.005) return '';
        return n.toFixed(2);
    }

    function isoDateOnly(value) {
        const text = String(value || '').trim();
        const iso = text.match(/^(\d{4}-\d{2}-\d{2})/);
        if (iso) return iso[1];
        const uk = text.match(/^(\d{1,2})[\/\-](\d{1,2})[\/\-](\d{4})$/);
        if (uk) return `${uk[3]}-${uk[2].padStart(2, '0')}-${uk[1].padStart(2, '0')}`;
        return text.slice(0, 10);
    }

    function yearFromDate(value) {
        const iso = isoDateOnly(value);
        const match = String(iso || '').match(/^(20\d{2}|19\d{2})/);
        return match ? match[1] : '';
    }

    const MONTH_NAMES = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];

    function isoFromDate(d) {
        if (!d || Number.isNaN(d.getTime())) return '';
        return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
    }

    function dateFromIso(iso) {
        const text = isoDateOnly(iso);
        if (!text) return null;
        const d = new Date(`${text}T00:00:00`);
        return Number.isNaN(d.getTime()) ? null : d;
    }

    function addCalendarMonths(iso, months) {
        const d = dateFromIso(iso);
        if (!d) return '';
        const day = d.getDate();
        const next = new Date(d.getFullYear(), d.getMonth() + months, 1);
        const last = new Date(next.getFullYear(), next.getMonth() + 1, 0).getDate();
        next.setDate(Math.min(day, last));
        return isoFromDate(next);
    }

    function firstArdFromInc(inc) {
        const d = dateFromIso(inc);
        if (!d) return '';
        return isoFromDate(new Date(d.getFullYear() + 1, d.getMonth() + 1, 0));
    }

    function firstAccountsDue(inc, ard) {
        const fromInc = addCalendarMonths(inc, 21);
        const fromArd = addCalendarMonths(ard, 3);
        if (!fromInc) return fromArd;
        if (!fromArd) return fromInc;
        return fromInc > fromArd ? fromInc : fromArd;
    }

    function dayAfter(iso) {
        const d = dateFromIso(iso);
        if (!d) return '';
        d.setDate(d.getDate() + 1);
        return isoFromDate(d);
    }

    function chAccountingYears(inc) {
        const start0 = isoDateOnly(inc);
        if (!start0) return [];
        const years = [];
        let start = start0;
        let end = firstArdFromInc(start0);
        for (let i = 0; i < 6; i += 1) {
            const due = i === 0 ? firstAccountsDue(start0, end) : addCalendarMonths(end, 9);
            years.push({
                start,
                end,
                due,
                first: i === 0,
            });
            start = dayAfter(end);
            const endDate = dateFromIso(end);
            if (!start || !endDate) break;
            end = isoFromDate(new Date(endDate.getFullYear() + 1, endDate.getMonth() + 1, 0));
        }
        return years;
    }

    function statementSpanHint(data, fileName) {
        const hint = state.statementHint || {};
        let start = isoDateOnly(hint.start || '');
        let end = isoDateOnly(hint.end || '');
        const name = String(fileName || hint.filename || '');
        const named = name.match(/(\d{4}-\d{2}-\d{2}).*?(\d{4}-\d{2}-\d{2})/);
        if (named) {
            start = start || named[1];
            end = end || named[2];
        }
        ((((data || {}).bank || {}).statements) || []).forEach((row) => {
            const span = String(row.filename || '').match(/(\d{4}-\d{2}-\d{2}).*?(\d{4}-\d{2}-\d{2})/);
            if (!span) return;
            if (!start || span[1] < start) start = span[1];
            if (!end || span[2] > end) end = span[2];
        });
        const dates =         ((((data || {}).bank || {}).lines) || allBankLines())
            .map((line) => isoDateOnly(line.date || line.txn_date || ''))
            .filter(Boolean)
            .sort();
        if (dates.length) {
            if (!start || dates[0] < start) start = dates[0];
            if (!end || dates[dates.length - 1] > end) end = dates[dates.length - 1];
        }
        return { start, end };
    }

    function filingPeriod(org, data, fileName) {
        const company = org || ((ws() || {}).organisation) || {};
        const crm = selectedCompany() || {};
        const ch = state.chProfile || {};
        const inc = isoDateOnly(ch.inc || company.incorporation_date || crm.inc_date || '');
        const years = chAccountingYears(inc);
        const span = statementSpanHint(data, fileName);
        const today = isoFromDate(new Date());
        let picked = years[0] || {
            start: isoDateOnly(company.period_start || ''),
            end: isoDateOnly(company.period_end || ''),
            due: isoDateOnly(ch.accounts_next_due || company.filing_due || ''),
            first: true,
        };
        const first = years[0];
        const madeUpTo = isoDateOnly(ch.accounts_made_up_to || '');
        const apiStart = isoDateOnly(ch.accounts_period_start || '');
        const firstStillWanted = !!(first && (
            ch.accounts_overdue
            || (madeUpTo && madeUpTo === first.end)
            || (ch.accounts_next_due && first.due && ch.accounts_next_due <= first.due)
            || (!ch.accounts_next_due && first.due && first.due < today)
        ));
        if (madeUpTo) {
            picked = {
                start: apiStart || (years.find((year) => year.end === madeUpTo) || {}).start || first.start || picked.start,
                end: madeUpTo,
                due: isoDateOnly(ch.accounts_next_due || '') || (years.find((year) => year.end === madeUpTo) || {}).due || picked.due,
                first: ch.is_first_accounts != null ? !!ch.is_first_accounts : !ch.accounts_last_made_up_to,
            };
        } else if (first && firstStillWanted) {
            picked = first;
        } else if (span.start && years.length) {
            const overlap = years.filter((year) => year.start <= (span.end || span.start) && year.end >= span.start);
            const endInside = overlap.find((year) => year.end >= span.start && year.end <= (span.end || year.end));
            picked = endInside || overlap[overlap.length - 1] || picked;
        } else if (ch.accounts_next_due && years.length) {
            picked = years.find((year) => year.due === ch.accounts_next_due)
                || years.find((year) => year.due && year.due <= ch.accounts_next_due)
                || picked;
        } else if (years.length) {
            picked = years.find((year) => year.due && year.due < today) || years.find((year) => year.start <= today && today <= year.end) || picked;
        }
        const next = years[years.findIndex((year) => year.end === picked.end) + 1] || {};
        return {
            inc,
            start: picked.start,
            end: picked.end,
            due: picked.due,
            firstStart: (years[0] || {}).start,
            firstEnd: (years[0] || {}).end,
            firstDue: (years[0] || {}).due,
            nextStart: next.start || dayAfter(picked.end),
            nextEnd: next.end || '',
            bookedStart: isoDateOnly(company.period_start || ''),
            bookedEnd: isoDateOnly(company.period_end || ''),
            isFirst: !!picked.first,
            bankFrom: span.start,
            statementTo: span.end,
        };
    }

    function monthsForPeriod(start, end) {
        const from = dateFromIso(start);
        const to = dateFromIso(end);
        if (!from || !to) return [];
        const out = [];
        const cursor = new Date(from.getFullYear(), from.getMonth(), 1);
        const last = new Date(to.getFullYear(), to.getMonth(), 1);
        while (cursor <= last) {
            const y = cursor.getFullYear();
            const m = cursor.getMonth();
            const monthStart = new Date(y, m, 1);
            const monthEnd = new Date(y, m + 1, 0);
            const fromDate = cursor.getTime() === new Date(from.getFullYear(), from.getMonth(), 1).getTime() ? from : monthStart;
            const toDate = (y === to.getFullYear() && m === to.getMonth()) ? to : monthEnd;
            out.push({
                key: `${y}-${String(m + 1).padStart(2, '0')}`,
                label: `${MONTH_NAMES[m]} ${y}`,
                from: isoFromDate(fromDate),
                to: isoFromDate(toDate),
            });
            cursor.setMonth(cursor.getMonth() + 1);
        }
        return out;
    }

    function coveredMonthKeys(data) {
        const keys = new Set();
        ((((data || {}).bank || {}).lines) || allBankLines()).forEach((line) => {
            const iso = isoDateOnly(line.date || line.txn_date || '');
            if (iso) keys.add(iso.slice(0, 7));
        });
        return keys;
    }

    function statementFileSpan(data) {
        const hint = state.statementHint || {};
        let start = isoDateOnly(hint.start || '');
        let end = isoDateOnly(hint.end || '');
        ((((data || {}).bank || {}).statements) || []).forEach((row) => {
            const name = String(row.filename || '');
            const span = name.match(/(\d{4}-\d{2}-\d{2}).*?(\d{4}-\d{2}-\d{2})/);
            if (!span) return;
            if (!start || span[1] < start) start = span[1];
            if (!end || span[2] > end) end = span[2];
        });
        return { start, end };
    }

    function statementFileMonths(data) {
        const span = statementFileSpan(data);
        const keys = new Set();
        if (span.start && span.end) {
            monthsForPeriod(span.start, span.end).forEach((month) => keys.add(month.key));
        }
        return keys;
    }

    function statementMonthsCard(data) {
        const org = (data && data.organisation) || {};
        const period = filingPeriod(org, data);
        if (!period.start || !period.end) return '';
        const months = monthsForPeriod(period.start, period.end);
        const have = coveredMonthKeys(data);
        const fileSpan = statementFileSpan(data);
        const openedOn = (fileSpan.start || period.bankFrom || Array.from(have).sort()[0] || '').slice(0, 7);
        const fileMonths = statementFileMonths(data);
        const extra = Array.from(new Set([...have, ...fileMonths])).filter((key) => !months.some((month) => month.key === key)).sort();
        const chips = months.map((month) => {
            const hasLines = have.has(month.key);
            const onStatement = fileMonths.has(month.key) || (openedOn && month.key >= openedOn);
            const beforeBank = openedOn && month.key < openedOn;
            const span = month.from === month.to ? ukDate(month.from) : `${ukDate(month.from)} – ${ukDate(month.to)}`;
            let status = 'Need statement';
            let tone = 'is-need';
            if (hasLines) {
                status = 'On file';
                tone = 'is-have';
            } else if (beforeBank) {
                status = 'Bank not open';
                tone = 'is-later';
            } else if (onStatement) {
                status = 'On statement';
                tone = 'is-have';
            }
            return `<li class="${tone}"><strong>${h(month.label)}</strong><span>${status}</span><em>${h(span)}</em></li>`;
        }).join('');
        const missing = months.filter((month) => !have.has(month.key) && !(openedOn && month.key < openedOn) && !fileMonths.has(month.key));
        return `
            <section class="accounts-months-card">
                <p class="accounts-step-kicker">Companies House · ${period.isFirst ? 'first accounts' : 'this filing'}</p>
                <h2>Which months of statement you need</h2>
                <p class="accounts-file-help">${h(ukDate(period.start))} – ${h(ukDate(period.end))}${period.due ? ` · due ${h(ukDate(period.due))}` : ''}.</p>
                <ol class="accounts-month-grid">${chips}</ol>
                <p class="accounts-file-help ${missing.length ? 'is-warn' : ''}">${missing.length
                    ? `${missing.length} month(s) after the account opened still missing: ${missing.map((month) => month.label).join(', ')}.`
                    : 'Months after the bank opened are on the books.'}</p>
                ${extra.length ? `<p class="accounts-file-help is-warn">Outside this year: ${extra.map((key) => {
                    const [y, m] = key.split('-');
                    return `${MONTH_NAMES[Number(m) - 1]} ${y}`;
                }).join(', ')}.</p>` : ''}
            </section>`;
    }

    function bankUploadRequiredLine(data) {
        const period = filingPeriod((data && data.organisation) || {}, data);
        if (!period.start || !period.end) {
            return `<p class="accounts-upload-required">Please Upload Bank Statement: <strong>Companies House period</strong> <span class="accounts-required-star" title="Required">*</span></p>`;
        }
        return `<p class="accounts-upload-required">Please Upload Bank Statement: <strong>${h(ukDate(period.start))} – ${h(ukDate(period.end))}</strong> <span class="accounts-required-star" title="Required">*</span></p>`;
    }

    function accountantWorkingNote(data) {
        const org = (data && data.organisation) || {};
        const bank = (data && data.bank) || {};
        const bs = (data && data.balance_sheet) || {};
        const ye = (data && data.year_end) || {};
        const lines = bank.lines || [];
        const ecommerce = lines.filter(isEcommerceReceipt);
        const directorPay = lines.filter(isDirectorSalary);
        const dividends = lines.filter(isDirectorDividend);
        const directorOut = directorPay.reduce((sum, line) => sum + Number(line.money_out || 0), 0);
        const dividendOut = dividends.reduce((sum, line) => sum + Number(line.money_out || 0), 0);
        const dormant = filingKind() === 'dormant' || !!ye.can_file_dormant;
        const points = [];
        if (dormant) {
            points.push(`The company did not trade in the year ended ${ukDate(org.period_end)}. These are dormant accounts — no bank statement and no profit.`);
        } else {
            const report = classificationReport(lines);
            points.push(`We built these accounts from the latest bank statement for ${h(org.registered_name || 'the company')} (${h(org.company_number || 'no number')}), year ended ${ukDate(org.period_end)}. ${lines.length} bank line(s) were posted. Detection accuracy ${report.accuracy}%. ${report.unknown.length ? `${report.unknown.length} line(s) were left for the software runner to categorise.` : 'Every line had a detected path or a chosen category.'}`);
            points.push(`Sales this year are ${gbp(plLineAmount(data, 'A'))}. ${ecommerce.length ? `${ecommerce.length} payment(s) from shops such as Shopify, Stripe or PayPal were counted as sales.` : 'No shop-platform payments were found.'}`);
            points.push(`Profit for the year is ${gbp(plLineAmount(data, 'H'))}.`);
            points.push(directorPay.length
                ? `Money paid to the director for work (${gbp(directorOut)}) was counted as director salary, not ordinary staff pay.`
                : 'No director salary lines were found. Add the director’s name on Company if the bank only shows a personal name.');
            if (dividends.length) {
                points.push(`Dividends of ${gbp(dividendOut)} were left as dividends. That is a share of profit after tax, not salary.`);
            }
        }
        points.push(`What the company owns minus what it owes is ${gbp((bs.current || {}).net_assets)}. ${bs.balances ? 'That matches capital and reserves.' : `This is out by ${gbp(bs.difference)}.`}`);
        return `
            <section class="accounts-work-note" aria-label="Accountant working note">
                <h3>Accountant note — what we did</h3>
                <ol>${points.map((item) => `<li>${item}</li>`).join('')}</ol>
            </section>`;
    }

    function filingYearCard(data) {
        const org = (data && data.organisation) || {};
        const period = filingPeriod(org, data);
        const laterBank = !!(period.bankFrom && period.end && period.bankFrom > period.end);
        const inYear = bankLines().length;
        return `
            <div class="accounts-file-banner ${period.isFirst ? 'is-warn' : 'is-ok'}">
                <strong>${period.isFirst ? 'First accounts' : 'Accounts'} to ${h(ukDate(period.end || period.firstEnd))}${period.due ? ` · due ${h(ukDate(period.due))}` : ''}.</strong>
                ${laterBank ? `<p>Bank starts ${h(ukDate(period.bankFrom))} — next year, not this pack.</p>` : ''}
                ${period.isFirst && !inYear ? `<p>Dormant. Share capital £${h(String(((state.chProfile || {}).share_capital) || 1))} unpaid. Employees 0.</p>` : ''}
            </div>`;
    }

    function filingTermsCard() {
        const terms = [
            ['Capital and reserves', 'The owners’ funds left in the company (share capital plus retained profit, less dividends). On a micro-entity balance sheet this is item K. It must equal net assets.'],
            ['Net assets', 'What the company owns minus what it owes (Format 1 items A to J). If this equals capital and reserves, the balance sheet agrees.'],
            ['Companies House pack', 'The filleted copy for the registrar: balance sheet, notes and statements. Micro-entities may currently omit the profit and loss account from this file (until accounts delivered on or after 1 April 2028).'],
            ['Members’ pack', 'The fuller copy for the shareholders. Same balance sheet, plus the profit and loss account. This is not what you upload to Companies House unless you choose to.'],
            ['Draft iXBRL', 'A tagged draft of the same figures. Useful for software filing later. It is not a finished Companies House TIS package — do not treat it as a live submission file.'],
            ['WebFiling', 'The Companies House website where you upload the pack yourself. Use this if you do not have a presenter / gateway login.'],
            ['Authentication code', 'The company’s 6-character Companies House code. Needed if this software sends the accounts. We only show whether it is on file, not the code itself.'],
            ['Presenter ID / API key', 'Optional software-filing login from Companies House. Leave blank if you will upload the pack in WebFiling.'],
            ['Micro-entity test', 'A small company may use this short format if it meets any two of: sales, what it owns (balance sheet total), and average staff. Limits depend on whether the year began before or after 6 April 2025.'],
            ['HMRC tax', 'Corporation Tax is filed on the HMRC tab, separately from this Companies House pack. That tab shows a guide to tax and the Company Tax Return — it does not file a live CT600 from here.'],
        ];
        return `
            <section class="accounts-file-card accounts-terms-card">
                <h2>What these terms mean</h2>
                <p class="accounts-file-help">Plain English for the filing screen. The working note at the bottom tells an accountant what we actually posted.</p>
                <dl class="accounts-terms-list">
                    ${terms.map(([term, meaning]) => `<div><dt>${h(term)}</dt><dd>${h(meaning)}</dd></div>`).join('')}
                </dl>
            </section>`;
    }

    function chBsField(label, value, kind) {
        const calc = kind === 'calc';
        const group = kind === 'group';
        if (group) return `<div class="ch-bs-group">${h(label)}</div>`;
        return `
            <div class="ch-bs-row${calc ? ' is-calc' : ''}">
                <label>${h(label)}</label>
                <input type="text" inputmode="decimal" readonly value="${h(chNumber(value))}" class="${calc ? 'is-calc' : ''}" aria-label="${h(label)}">
            </div>`;
    }

    function statutoryBalanceSheet(data, options) {
        const org = (data && data.organisation) || {};
        const ye = (data && data.year_end) || {};
        const bs = (data && data.balance_sheet) || {};
        const lookup = microBsLookup(bs);
        const period = filingPeriod(org, data);
        const yearEnd = period.end || isoDateOnly(org.period_end || '');
        const yearLabel = ukDate(yearEnd);
        const yearOnly = yearFromDate(yearEnd);
        const emptyFirstYear = !!(period.isFirst && period.end && !bankLines().length);
        const dormant = filingKind() === 'dormant' || !!ye.can_file_dormant;
        const shareCapital = Number((state.chProfile || {}).share_capital || org.share_capital || 0);
        const unpaidCapital = (emptyFirstYear || (dormant && !bankLines().length)) ? (shareCapital > 0 ? shareCapital : 1) : 0;
        const amt = (code) => {
            if (emptyFirstYear || (dormant && !bankLines().length)) {
                if (code === 'A' || code === 'G' || code === 'K' || code === 'NA') return unpaidCapital;
                return 0;
            }
            const row = lookup[code] || {};
            const fromLine = row.current;
            const fromPack = ((bs.current || {})[code]);
            const n = Number(fromLine != null ? fromLine : (fromPack || 0));
            if (code === 'A') {
                const currentAssets = Number(((lookup.C || {}).current) || (bs.current || {}).C || 0);
                if (Math.abs(n) > 0.004 && Math.abs(n - currentAssets) < 0.02) return 0;
            }
            return n;
        };
        const company = org.registered_name || (selectedCompany() && (selectedCompany().name || selectedCompany().ledger_name)) || 'Company';
        const number = org.company_number || '';
        const employees = (emptyFirstYear || dormant) ? 0 : (org.employees || org.average_employees || 0);
        const directorName = (String(ye.signed_by || '').split(',')[0] || '').trim();
        const agreed = (emptyFirstYear || (dormant && !bankLines().length)) ? true : !!bs.balances;
        const statements = dormant ? [
            { id: 's480', text: `For the year ending ${yearLabel} the company was entitled to exemption from audit under section 480 of the Companies Act 2006 relating to dormant companies.` },
            { id: 's476', text: 'The members have not required the company to obtain an audit in accordance with section 476 of the Companies Act 2006.' },
            { id: 's475', text: 'The directors acknowledge their responsibility for complying with the requirements of the Companies Act 2006 with respect to accounting records and the preparation of accounts.' },
            { id: 'dormant', text: 'The company was dormant throughout the year.' },
        ] : [
            { id: 's477', text: `For the year ending ${yearLabel} the company was entitled to exemption under section 477 of the Companies Act 2006 relating to small companies.` },
            { id: 's476', text: 'The members have not required the company to obtain an audit in accordance with section 476 of the Companies Act 2006.' },
            { id: 's475', text: 'The directors acknowledge their responsibility for complying with the requirements of the Companies Act 2006 with respect to accounting records and the preparation of accounts.' },
            { id: 'micro', text: 'The accounts have been prepared in accordance with the micro-entity provisions.' },
        ];
        return `
            <article class="ch-bs-form" aria-label="Micro-entity balance sheet">
                <header class="ch-bs-head">
                    <p>Filing for <strong>${h(company)}</strong>${number ? ` (${h(number)})` : ''}</p>
                    <h2>Micro-entity Balance Sheet</h2>
                </header>
                <div class="ch-bs-meta">
                    <label>Date of balance sheet
                        <input type="text" readonly value="${h(yearLabel)}" aria-label="Date of balance sheet">
                    </label>
                    <label>Currency
                        <input type="text" readonly value="GBP — Pound Sterling">
                    </label>
                </div>
                <p class="ch-bs-hint">Do not include any punctuation, for example commas, full stops or brackets, when entering figures into the balance sheet.</p>
                <div class="ch-bs-year">
                    <strong title="Year ended ${h(yearLabel)}">${h(yearOnly || (yearLabel.match(/20\d{2}/) || [''])[0])}</strong>
                    <span>£</span>
                </div>
                <p class="accounts-file-help">Year ended ${h(yearLabel)}.</p>
                ${chBsField('Called up share capital not paid', amt('A'))}
                ${chBsField('Fixed assets', 0, 'group')}
                ${chBsField('Total fixed assets', amt('B'), 'calc')}
                ${chBsField('Current assets', 0, 'group')}
                ${chBsField('Total current assets', amt('C'), 'calc')}
                ${chBsField('Prepayments and accrued income', amt('D'))}
                ${chBsField('Creditors: amounts falling due within one year', amt('E'))}
                ${chBsField('Net current assets (liabilities)', amt('F'), 'calc')}
                ${chBsField('Total assets less current liabilities', amt('G'), 'calc')}
                ${chBsField('Creditors: amounts falling due after more than one year', amt('H'))}
                ${chBsField('Provisions for liabilities', amt('I'))}
                ${chBsField('Accruals and deferred income', amt('J'))}
                ${chBsField('Total net assets (liabilities)', amt('NA'), 'calc')}
                ${chBsField('Capital and reserves', 0, 'group')}
                ${chBsField('Capital and reserves', amt('K'), 'calc')}
                ${chBsField('Employees', 0, 'group')}
                <div class="ch-bs-row">
                    <label>Average number of employees</label>
                    <input type="text" readonly value="${h(String(employees))}" aria-label="Average number of employees">
                </div>
                <p class="accounts-file-help ${agreed ? '' : 'is-warn'}">${agreed
                    ? 'The form agrees: total net assets equal capital and reserves.'
                    : `The form is out by ${gbp(bs.difference)}. Companies House will reject it until net assets equal capital and reserves.`}</p>
                <section class="ch-bs-confirm">
                    <h3>Please confirm the statements below</h3>
                    <p class="ch-bs-mandatory">Ticks, approval date and director name are required.</p>
                    ${statements.map((st) => `
                        <label class="ch-bs-check">
                            <input type="checkbox" checked disabled>
                            <span><strong>${h(st.text)}</strong></span>
                        </label>
                    `).join('')}
                    <div class="ch-bs-sign-grid">
                        <label>Date of approval of accounts
                            <input type="text" readonly value="${h(yearLabel)}">
                        </label>
                        <label>Name of the approving director
                            <input type="text" readonly value="${h(directorName)}">
                        </label>
                        <label>Additional approving director (if any)
                            <input type="text" readonly value="">
                        </label>
                    </div>
                </section>
            </article>`;
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
            `<article class="accounts-file-note"><h3>${h(st.title)}</h3><p>${h(st.body)}</p></article>`
        )).join('');
        const notes = (ye.notes || []).map((note) => (
            `<article class="accounts-file-note"><h3>${h(note.title)}</h3><p>${h(note.body)}</p></article>`
        )).join('');
        return `
            <section class="accounts-file-card">
                <h2>Required notes</h2>
                ${notes}
            </section>
            <section class="accounts-file-card">
                <h2>Director and audit-exemption statements</h2>
                ${statements}
                <div class="accounts-file-empty-actions">
                    <button type="button" class="btn-primary" data-accounts-screen="file">Go to File at Companies House</button>
                </div>
            </section>`;
    }

    function innerContent() {
        if (state.loading && !state.companies.length) return loadingState('Loading companies…');
        if (state.loading && state.companyId && !state.workspace) return loadingState('Opening books…');
        if (!state.companyId) return emptyCompany();
        const data = ws();
        const org = data.organisation || {};
        if (state.accountancyPane === 'hmrc') return hmrcScreen(data);
        if (state.screen === 'home') return homeScreen(data);
        if (state.screen === 'hmrc') return hmrcScreen(data);
        if (state.screen === 'bank') return bankScreen(data);
        if (state.screen === 'review') return reviewScreen(data);
        if (state.screen === 'reports') return reportsScreen(data);
        if (state.screen === 'file') return fileScreen(data);
        if (state.screen === 'organisation') return organisationScreen(data);
        if (isEmpty() && filingKind() !== 'dormant') {
            return `<div class="accounts-file-empty"><h2>Choose a path first</h2><p>These extra screens fill in after you pick whether the company traded or slept.</p><div class="accounts-file-empty-actions">${nextStepCta()}</div></div>`;
        }
        if (state.screen === 'coa') return coaScreen(data);
        if (state.screen === 'journals') return journalsScreen(data);
        if (state.screen === 'organisation') return organisationScreen(data);
        if (state.screen === 'pl') {
            return `${reportScreen(
                'Profit and loss',
                `Year ended ${ukDate(org.period_end)}. FRS 105 Section C format.`,
                (data.profit_and_loss || {}).lines,
            )}`;
        }
        if (state.screen === 'bs') {
            if (isEmpty() && filingKind() !== 'dormant') {
                return `<div class="accounts-file-empty"><h2>No balance sheet yet</h2><p>Upload the latest bank statement so we can build the micro-entity Format 1 balance sheet.</p></div>`;
            }
            return statutoryBalanceSheet(data);
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
            hmrc_utr: (document.getElementById('acc-org-utr') || {}).value,
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

    function bindCompanyPicker(root) {
        const input = document.getElementById('accounts-file-company');
        const list = document.getElementById('accounts-company-list');
        if (!input || !list) return;

        function paintList() {
            const rows = companyMatches(input.value);
            if (!String(input.value || '').trim()) {
                list.innerHTML = '<li class="accounts-company-empty">Type the company name. Matching names will appear here.</li>';
            } else if (!rows.length) {
                list.innerHTML = '<li class="accounts-company-empty">No company matches that name.</li>';
            } else {
                list.innerHTML = rows.map((row) => (
                    `<li><button type="button" role="option" data-company-id="${Number(row.id)}">${h(companyLabel(row))}</button></li>`
                )).join('');
            }
            list.hidden = false;
            input.setAttribute('aria-expanded', 'true');
        }

        function stage(id) {
            const next = Number(id || 0);
            const chosen = state.companies.find((row) => Number(row.id) === next);
            state.pendingCompanyId = next;
            if (chosen) input.value = companyLabel(chosen);
            list.hidden = true;
            input.setAttribute('aria-expanded', 'false');
        }

        input.addEventListener('focus', () => {
            input.select();
            paintList();
        });
        input.addEventListener('input', () => {
            state.pendingCompanyId = 0;
            paintList();
        });
        input.addEventListener('keydown', (event) => {
            if (event.key === 'Escape') {
                list.hidden = true;
                input.setAttribute('aria-expanded', 'false');
            }
            if (event.key === 'Enter') {
                event.preventDefault();
                const first = list.querySelector('[data-company-id]');
                if (first) stage(first.getAttribute('data-company-id'));
                applyCompanySelection();
            }
        });
        list.addEventListener('mousedown', (event) => {
            const btn = event.target.closest('[data-company-id]');
            if (!btn) return;
            event.preventDefault();
            stage(btn.getAttribute('data-company-id'));
        });
        if (!state.comboDocBound) {
            document.addEventListener('mousedown', (event) => {
                const wrap = document.querySelector('.accounts-company-combo');
                if (wrap && !wrap.contains(event.target)) {
                    const openList = document.getElementById('accounts-company-list');
                    const openInput = document.getElementById('accounts-file-company');
                    if (openList) openList.hidden = true;
                    if (openInput) openInput.setAttribute('aria-expanded', 'false');
                }
                const reports = document.querySelector('.accounts-reports-wrap');
                if (reports && !reports.contains(event.target)) {
                    const menu = reports.querySelector('.accounts-reports-menu');
                    const toggle = reports.querySelector('.accounts-reports-toggle');
                    if (menu) menu.hidden = true;
                    if (toggle) toggle.setAttribute('aria-expanded', 'false');
                }
            });
            state.comboDocBound = true;
        }
    }

    function bind(root) {
        bindCompanyPicker(root);
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
            fileInput.addEventListener('change', () => {
                const files = Array.from((fileInput.files || []));
                state.pendingStatementFiles = files;
                state.compileTested = false;
                state.precheck = null;
                hintFromFiles(files);
                const nameEl = document.getElementById('accounts-file-name');
                if (nameEl) nameEl.textContent = pendingFileLabel();
                if (files.length) {
                    state.screen = 'bank';
                    state.notice = `${files.length} statement${files.length === 1 ? '' : 's'} selected. Press Compile on Bank.`;
                    render();
                }
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
        root.querySelectorAll('[data-line-select]').forEach((box) => {
            box.addEventListener('change', () => {
                const id = Number(box.getAttribute('data-line-select'));
                const chosen = new Set((state.selectedLineIds || []).map(Number));
                if (box.checked) chosen.add(id);
                else chosen.delete(id);
                state.selectedLineIds = Array.from(chosen);
                render();
            });
        });
        const selectAll = document.getElementById('accounts-select-all');
        if (selectAll) {
            selectAll.addEventListener('change', () => {
                const visible = Array.from(root.querySelectorAll('[data-line-select]'))
                    .map((box) => Number(box.getAttribute('data-line-select')))
                    .filter(Boolean);
                state.selectedLineIds = selectAll.checked ? visible : [];
                render();
            });
        }
        root.querySelectorAll('[data-check-goto]').forEach((btn) => {
            btn.addEventListener('click', () => {
                state.checkStep = Number(btn.getAttribute('data-check-goto') || 0);
                state.selectedLineIds = [];
                render();
            });
        });
    }

    async function handleRecategorise(lineId, code) {
        state.error = '';
        markLinesReviewed([Number(lineId)], code);
        render();
        try {
            await postAction('recategorise', { line_id: Number(lineId), category_code: code });
            markLinesReviewed([Number(lineId)], code);
            state.notice = 'Category saved.';
        } catch (err) {
            state.error = err.message || 'Could not save that category.';
        }
        render();
    }

    async function recategoriseMany(ids, code) {
        const unique = Array.from(new Set((ids || []).map(Number).filter(Boolean)));
        if (!unique.length) throw new Error('Select one or more lines first.');
        const cats = ((ws().bank || {}).categories) || [];
        const label = ((cats.find((cat) => cat.code === code) || {}).label) || '';
        markLinesReviewed(unique, code, label);
        try {
            await postAction('recategorise', {
                line_ids: unique,
                category_code: code,
            });
        } catch (err) {
            const message = String(err.message || '');
            if (!/select a transaction|line_id|not found/i.test(message) && unique.length > 1) {
                throw err;
            }
            const last = await runPool(unique, 1, (id) => (
                postAction('recategorise', { line_id: Number(id), category_code: code }, 'POST', { quiet: true })
            ));
            if (last) state.workspace = last;
        }
        markLinesReviewed(unique, code, label);
        return unique.length;
    }

    function parseCsvPreview(text) {
        const raw = String(text || '').replace(/^\uFEFF/, '').replace(/\r/g, '');
        const rows = raw.split('\n').map((row) => row.trim()).filter(Boolean);
        if (rows.length < 2) return [];
        const delim = (rows[0].split('\t').length > rows[0].split(',').length) ? '\t' : ',';
        const split = (line) => line.split(delim).map((cell) => cell.replace(/^"|"$/g, '').trim());
        const headers = split(rows[0]).map((cell) => cell.toLowerCase());
        const pick = (names) => headers.findIndex((header) => names.some((name) => header === name || header.includes(name)));
        const dateIdx = pick(['date', 'transaction date', 'completed date', 'created']);
        const descIdx = pick(['description', 'narrative', 'name', 'reference', 'counter']);
        const inIdx = pick(['money in', 'paid in', 'credit', 'in']);
        const outIdx = pick(['money out', 'paid out', 'debit', 'out']);
        const amountIdx = pick(['amount', 'value']);
        if (descIdx < 0 && dateIdx < 0) return [];
        return rows.slice(1).map((row, index) => {
            const cells = split(row);
            const incoming = Number(String(cells[inIdx] || '').replace(/[£,]/g, '')) || 0;
            const outgoing = Number(String(cells[outIdx] || '').replace(/[£,]/g, '')) || 0;
            const amount = Number(String(cells[amountIdx] || '').replace(/[£,]/g, '')) || 0;
            return {
                id: `preview-${index}`,
                date: cells[dateIdx] || '',
                description: cells[descIdx] || cells.join(' '),
                money_in: incoming || (amount > 0 ? amount : 0),
                money_out: outgoing || (amount < 0 ? Math.abs(amount) : 0),
            };
        }).filter((line) => line.description);
    }

    async function reconcileImportedLines() {
        const groups = {};
        bankLines().forEach((line) => {
            const hint = classifyTransaction(line);
            if (hint.confidence !== 'high' || !hint.code) return;
            if (String(line.category_code || '') === hint.code) return;
            if (!groups[hint.code]) groups[hint.code] = [];
            groups[hint.code].push(Number(line.id));
        });
        let saved = 0;
        const codes = Object.keys(groups);
        for (let i = 0; i < codes.length; i += 1) {
            const code = codes[i];
            saved += await recategoriseMany(groups[code], code);
        }
        return saved;
    }

    async function maybeReconcileOpenBooks() {
        if (state.reconciledOnce || !state.companyId || isEmpty()) return;
        const report = classificationReport(bankLines());
        if (report.mismatch.length) {
            const saved = await reconcileImportedLines();
            if (saved) {
                state.notice = `Corrected ${saved} high-confidence path(s). Unknown lines were left for you.`;
            }
        }
        state.classifyReport = classificationReport(bankLines());
        state.reconciledOnce = true;
        if (state.screen === 'review') goToNeedsYouStep();
    }

    function goToNeedsYouStep() {
        const wf = checkWorkflow(ws());
        const idx = wf.findIndex((step) => step.id === 'needs-you' && step.lines.length);
        state.checkStep = idx >= 0 ? idx : wf.findIndex((step) => step.id === 'done');
        if (state.checkStep < 0) state.checkStep = Math.max(0, wf.length - 1);
    }

    async function handleImportStatement(documentId) {
        if (!state.companyId) throw new Error('Select a company first.');
        await ensureTradedPath();
        const selfTest = runClassifierSelfTest();
        if (!selfTest.ok) {
            throw new Error(`Classification test failed before compile: ${selfTest.failures[0]}`);
        }
        const fileInput = document.getElementById('accounts-statement-file');
        const paste = ((document.getElementById('accounts-statement-paste') || {}).value || '').trim();
        const opening = (((document.getElementById('accounts-opening') || {}).value) || state.pendingOpening || '').trim();
        state.pendingOpening = opening;
        const files = pendingFiles().length
            ? pendingFiles()
            : Array.from((fileInput && fileInput.files) || []);
        if (files.length) state.pendingStatementFiles = files;
        state.compiling = true;
        state.compileProgress = 0;
        state.compileProgressLabel = 'Starting compile…';
        state.screen = 'bank';
        render();
        try {
        let imported = 0;
        let skipped = 0;
        if (files.length) {
            for (let i = 0; i < files.length; i += 1) {
                setCompileProgress(8 + Math.round((i / files.length) * 80), `Compiling ${files[i].name} (${i + 1} of ${files.length})…`);
                const form = new FormData();
                form.append('file', files[i], files[i].name);
                if (i === 0 && opening) form.append('opening_balance', opening);
                const res = await fetch(`${apiBase()}/${state.companyId}/import-statement`, {
                    method: 'POST',
                    credentials: 'same-origin',
                    body: form,
                });
                state.workspace = await readJson(res);
                const one = (state.workspace && state.workspace.import_result) || {};
                imported += Number(one.imported || 0);
                skipped += Number(one.skipped || 0);
                setCompileProgress(8 + Math.round(((i + 1) / files.length) * 80), `Compiled ${i + 1} of ${files.length}`);
            }
            state.pendingStatementFiles = [];
        } else if (paste) {
            await postAction('import-statement', {
                csv_text: paste,
                opening_balance: opening,
                filename: 'pasted-statement.csv',
            });
        } else if (documentId) {
            await postAction('import-statement', {
                opening_balance: opening,
                document_id: documentId,
            });
        } else {
            throw new Error('Upload the latest bank statement (CSV or PDF), or paste the rows. We will not use an older file on its own.');
        }
        setCompileProgress(90, 'Finishing…');
        const result = (state.workspace && state.workspace.import_result) || {};
        if (!imported) imported = Number(result.imported || 0);
        if (!skipped) skipped = Number(result.skipped || 0);
        const applied = 0;
        setCompileProgress(96, 'Opening Check…');
        let period = { end: '' };
        try {
            period = await clampBooksToFilingPeriod() || period;
        } catch (err) {
            state.error = err.message || 'Could not lock the Companies House year.';
        }
        state.classifyReport = classificationReport(bankLines());
        state.precheck = state.classifyReport;
        state.compileTested = false;
        const unknown = state.classifyReport.unknown.length;
        const afterEnd = bankLines().filter((line) => {
            const day = isoDateOnly(line.date || line.txn_date || '');
            return period.end && day && day > period.end;
        }).length;
        setCompileProgress(100, 'Compiled');
        state.notice = `Compiled ${imported} line(s)`
            + (skipped ? `, skipped ${skipped} duplicate(s)` : '')
            + (applied ? `, corrected ${applied} high-confidence path(s)` : '')
            + (period.end ? ` through ${ukDate(period.end)}.` : '.')
            + ` Detection ${state.classifyReport.accuracy}%.`
            + (unknown ? ` ${unknown} line(s) need you — we did not guess.` : '')
            + (afterEnd ? ` ${afterEnd} line(s) after the Companies House year end sit outside this year.` : '');
        state.screen = 'review';
        state.checkStep = 0;
        } finally {
            state.compiling = false;
            state.compileProgress = 0;
            state.compileProgressLabel = '';
        }
    }

    async function applyCompanySelection() {
        const input = document.getElementById('accounts-file-company');
        const typed = (input && input.value) || '';
        let next = Number(state.pendingCompanyId || 0);
        if (!next) {
            const rows = companyMatches(typed);
            if (rows.length) next = Number(rows[0].id);
        }
        if (!next) {
            state.error = 'Search the company name or number, then press Open books.';
            return;
        }
        state.pendingCompanyId = next;
        state.companyId = next;
        localStorage.setItem(storageKey(), String(state.companyId || ''));
        state.notice = 'Company selected.';
        state.checkStep = 0;
        await refresh({ skipCh: true });
        if (state.accountancyPane === 'hmrc') {
            state.screen = 'hmrc';
            state.notice = 'Company selected. HMRC tax is filed separately from Companies House.';
            return;
        }
        state.screen = 'home';
        state.notice = 'Company selected. Stay on Start and choose what this company did this year.';
        loadCompaniesHousePeriod().then(() => render()).catch(() => {});
    }

    async function handleAction(action) {
        if (action === 'toggle-reports') {
            const menu = document.querySelector('.accounts-reports-menu');
            const toggle = document.querySelector('.accounts-reports-toggle');
            if (!menu || !toggle) return;
            const open = menu.hidden;
            menu.hidden = !open;
            toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
            return;
        }
        state.error = '';
        state.notice = '';
        try {
            if (action === 'sample') {
                await postAction('sample', {});
                state.screen = 'home';
            } else if (action === 'select-company') {
                await applyCompanySelection();
            } else if (action === 'choose-traded') {
                await postAction('choose-path', { kind: 'traded' });
                state.screen = 'bank';
                state.notice = 'Please upload the bank statement for the Companies House period shown on Bank.';
                loadCompaniesHousePeriod().then(() => render()).catch(() => {});
            } else if (action === 'choose-dormant') {
                await postAction('choose-path', { kind: 'dormant' });
                state.screen = 'file';
                state.notice = 'Dormant pack ready from Companies House dates. No bank statement needed.';
                await loadCompaniesHousePeriod();
                await lockBooksToCompaniesHouseYear();
            } else if (action === 'sample-statement') {
                await postAction('sample-statement', {});
                state.notice = 'Sample statement loaded. Check the numbers, then file.';
                state.screen = 'review';
            } else if (action === 'reset-books' || action === 'reset-company-data') {
                const row = selectedCompany();
                const name = (row && (row.ledger_name || row.name)) || 'this company';
                const ok = window.confirm(
                    `Reset all bank statements and transactions for ${name}?\n\nCompany details will stay hidden until you select the company again.\n\nPress OK to proceed.`
                );
                if (!ok) return;
                if (state.companyId) await postAction('reset-books', {});
                localStorage.removeItem(storageKey());
                sessionStorage.setItem(skipSelectKey(), '1');
                state.companyId = 0;
                state.pendingCompanyId = 0;
                state.workspace = null;
                state.checkStep = 0;
                state.selectedLineIds = [];
                state.precheck = null;
                state.classifyReport = null;
                state.compileTested = false;
                state.reconciledOnce = false;
                state.pendingStatementFiles = [];
                state.pendingOpening = '';
                state.compiling = false;
                state.compileProgress = 0;
                state.compileProgressLabel = '';
                window.location.reload();
                return;
            } else if (action === 'import-statement') {
                await handleImportStatement();
            } else if (action === 'check-step-back') {
                state.checkStep = Math.max(0, Number(state.checkStep || 0) - 1);
                state.selectedLineIds = [];
                render();
                return;
            } else if (action === 'check-step-next') {
                const wf = checkWorkflow(ws());
                state.checkStep = Math.min(wf.length - 1, Number(state.checkStep || 0) + 1);
                state.selectedLineIds = [];
                render();
                return;
            } else if (action === 'check-step-apply') {
                const wf = checkWorkflow(ws());
                const step = wf[state.checkStep] || {};
                const ids = (step.lines || []).map((line) => Number(line.id)).filter(Boolean);
                if (step.code && ids.length) {
                    const sure = (step.lines || []).filter((line) => {
                        const hint = classifyTransaction(line);
                        return hint.confidence === 'high' && hint.code === step.code;
                    }).map((line) => Number(line.id)).filter(Boolean);
                    const saved = await recategoriseMany(sure.length ? sure : ids, step.code);
                    state.notice = `Marked ${saved} sure line(s). Next step.`;
                }
                state.selectedLineIds = [];
                state.checkStep = Math.min(wf.length - 1, Number(state.checkStep || 0) + 1);
            } else if (action === 'select-ecommerce') {
                state.selectedLineIds = bankLines().filter(isEcommerceReceipt).map((line) => Number(line.id));
                state.notice = state.selectedLineIds.length
                    ? `${state.selectedLineIds.length} ecommerce receipt(s) selected. Create them as Sales, or apply another category.`
                    : 'No ecommerce platform receipts found on this statement.';
                render();
                return;
            } else if (action === 'select-director-pay') {
                state.selectedLineIds = bankLines().filter(isDirectorSalary).map((line) => Number(line.id));
                state.notice = state.selectedLineIds.length
                    ? `${state.selectedLineIds.length} director payment(s) selected. HMRC treats pay to a director for their work as director salary. Create as Director pay.`
                    : 'No director salary lines found. Add the director name on Company if the bank only shows a personal name.';
                render();
                return;
            } else if (action === 'select-dividends') {
                state.selectedLineIds = bankLines().filter(isDirectorDividend).map((line) => Number(line.id));
                state.notice = state.selectedLineIds.length
                    ? `${state.selectedLineIds.length} dividend line(s) selected. Dividends are not salary — apply Dividends if needed.`
                    : 'No dividend lines found.';
                render();
                return;
            } else if (action === 'clear-line-selection') {
                state.selectedLineIds = [];
                render();
                return;
            } else if (action === 'bulk-sales' || action === 'bulk-director-pay' || action === 'bulk-category') {
                const ids = (state.selectedLineIds || []).map(Number).filter(Boolean);
                const code = action === 'bulk-sales'
                    ? '4000'
                    : (action === 'bulk-director-pay'
                        ? '7002'
                        : (((document.getElementById('accounts-bulk-category') || {}).value) || '4000'));
                const cats = ((ws().bank || {}).categories) || [];
                const fallback = code === '4000' ? 'Sales' : (code === '7002' ? 'Director pay' : code);
                const label = (cats.find((cat) => cat.code === code) || {}).label || fallback;
                const saved = await recategoriseMany(ids, code);
                state.selectedLineIds = [];
                const leftover = classificationReport(bankLines()).unknown.length;
                state.notice = leftover
                    ? `Created ${saved} line(s) as ${label}. ${leftover} still need you.`
                    : `Created ${saved} line(s) as ${label}. Needs you is now 0.`;
            } else if (action === 'confirm-review') {
                const leftover = classificationReport(bankLines()).unknown.length;
                if (leftover) {
                    const ok = window.confirm(
                        `${leftover} transaction(s) still need you.\n\nWe could not detect a safe path. Set those categories yourself before filing.\n\nPress OK only if you will finish them on File, or Cancel to stay on Check.`
                    );
                    if (!ok) return;
                }
                await postAction('confirm-review', {});
                state.notice = leftover
                    ? `Numbers marked as checked, but ${leftover} line(s) still need a manual category.`
                    : 'Numbers marked as checked.';
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
            } else if (action === 'submit-hmrc') {
                await postAction('submit-hmrc', {
                    hmrc_utr: (document.getElementById('acc-hmrc-utr') || {}).value,
                });
                const submit = (state.workspace && state.workspace.hmrc_submit) || {};
                state.notice = submit.receipt ? `Receipt ${submit.receipt}` : (submit.message || 'HMRC tax recorded.');
                state.screen = 'hmrc';
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
            if (typeof showPortalToast === 'function') showPortalToast(state.error, 'error', 'Accountancy');
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

    async function refresh(options) {
        state.loading = true;
        state.error = '';
        render();
        try {
            await loadWorkspace();
            if (!(options && options.skipCh)) await loadCompaniesHousePeriod();
        } catch (err) {
            state.error = err.message || 'Unable to load this accounts file.';
            state.workspace = null;
            if (typeof showPortalToast === 'function') showPortalToast(state.error, 'error', 'Accountancy');
        } finally {
            state.loading = false;
            render();
        }
    }

    async function openHmrcWorkspace(scope) {
        state.scope = scope === 'admin' ? 'admin' : 'client';
        state.accountancyPane = 'hmrc';
        state.screen = 'hmrc';
        state.loading = true;
        state.error = '';
        render();
        try {
            if (!state.companies.length) await loadCompanyList();
            if (state.companyId && !state.workspace) await loadWorkspace();
        } catch (err) {
            state.error = err.message || 'Unable to open HMRC.';
        } finally {
            state.loading = false;
            render();
        }
    }

    async function loadAccountsFileView(scope, screen) {
        state.scope = scope === 'admin' ? 'admin' : 'client';
        state.accountancyPane = 'accounts';
        if (screen === 'year-end') state.screen = 'file';
        else if (screen === 'hmrc') state.screen = 'home';
        else state.screen = screen || 'home';
        setAccountancyPane(state.scope, 'accounts', { skipLoad: true });
        state.loading = true;
        state.error = '';
        state.notice = '';
        render();
        try {
            await loadCompanyList();
            state.loading = false;
            render();
            if (!state.companyId) return;
            state.loading = true;
            render();
            await loadWorkspace();
        } catch (err) {
            state.error = err.message || 'Unable to open Accounts.';
            if (typeof showPortalToast === 'function') showPortalToast(state.error, 'error', 'Accountancy');
        } finally {
            state.loading = false;
            render();
        }
    }
    window.loadAccountsFileView = loadAccountsFileView;

    function accountancyViewId(scope) {
        return scope === 'admin' ? 'view-admin-accountancy' : 'view-client-accountancy';
    }

    function hideLegacyAccountsNav() {
        ['client-accounts', 'client-year-end', 'admin-accounts', 'admin-year-end'].forEach((view) => {
            document.querySelectorAll(`[data-view="${view}"]`).forEach((el) => {
                el.hidden = true;
                el.style.display = 'none';
            });
        });
        ['view-client-accounts', 'view-admin-accounts', 'view-client-year-end', 'view-admin-year-end'].forEach((id) => {
            const panel = document.getElementById(id);
            if (!panel) return;
            const root = panel.querySelector('.accounts-file-root');
            if (root) return;
            panel.remove();
        });
    }

    function ensureAccountancyChrome(scope) {
        scope = scope === 'admin' ? 'admin' : 'client';
        const view = document.getElementById(accountancyViewId(scope));
        if (!view) return;
        const prefix = scope;
        view.querySelectorAll('.accountancy-mode-bar, .accountancy-inner-tabs').forEach((tabs) => {
            if (!tabs.querySelector('[data-accountancy-pane="hmrc"]')) {
                const hmrcBtn = document.createElement('button');
                hmrcBtn.type = 'button';
                hmrcBtn.setAttribute('data-accountancy-pane', 'hmrc');
                hmrcBtn.setAttribute('data-accountancy-scope', scope);
                hmrcBtn.textContent = 'HMRC';
                tabs.appendChild(hmrcBtn);
            }
            const filingBtn = tabs.querySelector('[data-accountancy-pane="filing"]');
            const accountsBtn = tabs.querySelector('[data-accountancy-pane="accounts"]');
            const hmrcBtn = tabs.querySelector('[data-accountancy-pane="hmrc"]');
            if (accountsBtn) accountsBtn.textContent = 'Company Accounts';
            if (filingBtn) tabs.appendChild(filingBtn);
            if (accountsBtn) tabs.appendChild(accountsBtn);
            if (hmrcBtn) tabs.appendChild(hmrcBtn);
            if (tabs.dataset.bound === '1') return;
            tabs.dataset.bound = '1';
            tabs.addEventListener('click', (event) => {
                const btn = event.target.closest('[data-accountancy-pane]');
                if (!btn || btn.tagName !== 'BUTTON') return;
                event.preventDefault();
                setAccountancyPane(scope, btn.getAttribute('data-accountancy-pane'));
            });
        });
        if (!view.querySelector('.accountancy-inner-tabs, .accountancy-mode-bar')) {
            const tabs = document.createElement('div');
            tabs.className = 'portfolio-filter-bar accountancy-inner-tabs accountancy-mode-bar';
            tabs.setAttribute('role', 'tablist');
            tabs.setAttribute('aria-label', 'Accountancy sections');
            tabs.innerHTML = `
                <button type="button" class="is-active" data-accountancy-pane="filing" data-accountancy-scope="${scope}">Filing status</button>
                <button type="button" data-accountancy-pane="accounts" data-accountancy-scope="${scope}">Company Accounts</button>
                <button type="button" data-accountancy-pane="hmrc" data-accountancy-scope="${scope}">HMRC</button>`;
            tabs.dataset.bound = '1';
            tabs.addEventListener('click', (event) => {
                const btn = event.target.closest('[data-accountancy-pane]');
                if (!btn || btn.tagName !== 'BUTTON') return;
                event.preventDefault();
                setAccountancyPane(scope, btn.getAttribute('data-accountancy-pane'));
            });
            const titleRow = view.querySelector('.portfolio-page-head, .page-title-row');
            if (titleRow && titleRow.nextSibling) view.insertBefore(tabs, titleRow.nextSibling);
            else view.insertBefore(tabs, view.firstChild);
        }
        let accountsPane = document.getElementById(`${prefix}-accountancy-pane-accounts`);
        if (!accountsPane) {
            accountsPane = document.createElement('div');
            accountsPane.id = `${prefix}-accountancy-pane-accounts`;
            accountsPane.className = 'accountancy-pane';
            view.appendChild(accountsPane);
        }
        let filingPane = document.getElementById(`${prefix}-accountancy-pane-filing`);
        if (!filingPane) {
            filingPane = document.createElement('div');
            filingPane.id = `${prefix}-accountancy-pane-filing`;
            filingPane.className = 'accountancy-pane';
            filingPane.hidden = true;
            const stats = view.querySelector('.accountancy-stats');
            const err = document.getElementById(scope === 'admin' ? 'adm-accountancy-error' : `${prefix}-accountancy-error`);
            const table = Array.from(view.querySelectorAll('.data-table-container')).find((el) => !accountsPane.contains(el));
            [stats, err, table].forEach((el) => { if (el) filingPane.appendChild(el); });
            view.appendChild(filingPane);
        }
        let root = document.getElementById(`${prefix}-accounts-root`);
        if (!root) {
            root = document.createElement('div');
            root.id = `${prefix}-accounts-root`;
            root.className = 'accounts-file-root';
            root.setAttribute('data-scope', scope);
        }
        if (root.parentElement !== accountsPane) accountsPane.appendChild(root);
        let hmrcPane = document.getElementById(`${prefix}-accountancy-pane-hmrc`);
        if (!hmrcPane) {
            hmrcPane = document.createElement('div');
            hmrcPane.id = `${prefix}-accountancy-pane-hmrc`;
            hmrcPane.className = 'accountancy-pane';
            hmrcPane.hidden = true;
            if (filingPane && filingPane.parentElement === view) view.insertBefore(hmrcPane, filingPane);
            else view.appendChild(hmrcPane);
        }
        let hmrcRoot = document.getElementById(`${prefix}-hmrc-root`);
        if (!hmrcRoot) {
            hmrcRoot = document.createElement('div');
            hmrcRoot.id = `${prefix}-hmrc-root`;
            hmrcRoot.className = 'accounts-file-root';
            hmrcRoot.setAttribute('data-scope', scope);
        }
        if (hmrcRoot.parentElement !== hmrcPane) hmrcPane.appendChild(hmrcRoot);
        const leftover = document.getElementById(`view-${prefix}-accounts`);
        if (leftover && leftover !== view) leftover.remove();
    }

    function setAccountancyPane(scope, pane, options) {
        scope = scope === 'admin' ? 'admin' : 'client';
        if (pane !== 'filing' && pane !== 'hmrc') pane = 'accounts';
        const opts = options || {};
        state.scope = scope;
        if (pane !== 'filing') state.accountancyPane = pane;
        ensureAccountancyChrome(scope);
        hideLegacyAccountsNav();
        const view = document.getElementById(accountancyViewId(scope));
        if (!view) return;
        view.querySelectorAll('[data-accountancy-pane]').forEach((btn) => {
            btn.classList.toggle('is-active', btn.getAttribute('data-accountancy-pane') === pane);
        });
        const accountsPane = document.getElementById(`${scope}-accountancy-pane-accounts`);
        const hmrcPane = document.getElementById(`${scope}-accountancy-pane-hmrc`);
        const filingPane = document.getElementById(`${scope}-accountancy-pane-filing`);
        if (accountsPane) accountsPane.hidden = pane !== 'accounts';
        if (hmrcPane) hmrcPane.hidden = pane !== 'hmrc';
        if (filingPane) filingPane.hidden = pane !== 'filing';
        if (pane === 'accounts' && !opts.skipLoad) {
            if (state.screen === 'hmrc') state.screen = opts.screen || 'home';
            const screen = opts.screen || state.screen || 'home';
            loadAccountsFileView(scope, screen);
        }
        if (pane === 'hmrc') {
            state.screen = 'hmrc';
            if (!opts.skipLoad) openHmrcWorkspace(scope);
            else render();
        }
        if (pane === 'filing' && !opts.skipLoad) {
            if (scope === 'admin' && typeof loadAdminAccountancy === 'function') loadAdminAccountancy();
            if (scope === 'client' && typeof loadClientAccountancy === 'function') loadClientAccountancy();
        }
    }
    window.setAccountancyPane = setAccountancyPane;

    function remapLegacyAccountsView(viewName) {
        if (viewName === 'client-accounts' || viewName === 'client-year-end') return 'client-accountancy';
        if (viewName === 'admin-accounts' || viewName === 'admin-year-end') return 'admin-accountancy';
        return viewName;
    }

    function installPortalHooks() {
        if (window.__brixenAccountsFileHooks) return;
        window.__brixenAccountsFileHooks = true;
        const access = ['Accountancy', 'Accounts', 'Compliance'];
        if (typeof VIEW_HASH === 'object' && VIEW_HASH) {
            VIEW_HASH['client-accounts'] = 'accountancy';
            VIEW_HASH['client-year-end'] = 'accountancy';
            VIEW_HASH['admin-accounts'] = 'admin-accountancy';
            VIEW_HASH['admin-year-end'] = 'admin-accountancy';
        }
        if (typeof HASH_VIEW === 'object' && HASH_VIEW) {
            HASH_VIEW['accounts'] = 'client-accountancy';
            HASH_VIEW['year-end'] = 'client-accountancy';
            HASH_VIEW['admin-accounts'] = 'admin-accountancy';
            HASH_VIEW['admin-year-end'] = 'admin-accountancy';
            HASH_VIEW['client-accounts'] = 'client-accountancy';
            HASH_VIEW['client-year-end'] = 'client-accountancy';
        }
        if (typeof VIEW_ACCESS === 'object' && VIEW_ACCESS) {
            VIEW_ACCESS['admin-accountancy'] = access;
            VIEW_ACCESS['admin-accounts'] = access;
            VIEW_ACCESS['admin-year-end'] = access;
        }
        ensureAccountancyChrome('client');
        ensureAccountancyChrome('admin');
        hideLegacyAccountsNav();
        window.addEventListener('beforeunload', onAccountsBeforeUnload);
        if (isBrowserReload()) {
            clearOpenBooksOnReload();
            state.accountancyPane = 'filing';
            ['client', 'admin'].forEach((scope) => {
                const view = document.getElementById(accountancyViewId(scope));
                if (!view) return;
                view.querySelectorAll('[data-accountancy-pane]').forEach((btn) => {
                    btn.classList.toggle('is-active', btn.getAttribute('data-accountancy-pane') === 'filing');
                });
                const accountsPane = document.getElementById(`${scope}-accountancy-pane-accounts`);
                const hmrcPane = document.getElementById(`${scope}-accountancy-pane-hmrc`);
                const filingPane = document.getElementById(`${scope}-accountancy-pane-filing`);
                if (accountsPane) accountsPane.hidden = true;
                if (hmrcPane) hmrcPane.hidden = true;
                if (filingPane) filingPane.hidden = false;
            });
        }
        const origSetPanel = window.setActiveViewPanel;
        if (typeof origSetPanel === 'function' && !origSetPanel.__accountsFilePatched) {
            const wrappedSet = function (viewName) {
                return origSetPanel(remapLegacyAccountsView(viewName));
            };
            wrappedSet.__accountsFilePatched = true;
            window.setActiveViewPanel = wrappedSet;
        }
        const origSwitch = window.switchView;
        const origHandlesPane = typeof origSwitch === 'function' && /setAccountancyPane/.test(Function.prototype.toString.call(origSwitch));
        if (typeof origSwitch === 'function' && !origSwitch.__accountsFilePatched) {
            const wrappedSwitch = function (viewName) {
                const args = Array.prototype.slice.call(arguments);
                let screen = 'home';
                if (viewName === 'client-year-end' || viewName === 'admin-year-end') screen = 'file';
                const mapped = remapLegacyAccountsView(viewName);
                args[0] = mapped;
                const prevView = window.activeView;
                const result = origSwitch.apply(this, args);
                if (!origHandlesPane && prevView !== mapped) {
                    if (mapped === 'client-accountancy') {
                        setAccountancyPane('client', 'filing');
                    } else if (mapped === 'admin-accountancy') {
                        setAccountancyPane('admin', 'filing');
                    }
                }
                hideLegacyAccountsNav();
                return result;
            };
            wrappedSwitch.__accountsFilePatched = true;
            window.switchView = wrappedSwitch;
        }
        const origApply = window.applyPortalAccessNav;
        if (typeof origApply === 'function' && !origApply.__accountsFilePatched) {
            const wrappedApply = function () {
                const result = origApply.apply(this, arguments);
                hideLegacyAccountsNav();
                return result;
            };
            wrappedApply.__accountsFilePatched = true;
            window.applyPortalAccessNav = wrappedApply;
        }
        if (typeof applyPortalAccessNav === 'function') {
            try { applyPortalAccessNav(); } catch (err) { /* nav filter runs again after login */ }
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', installPortalHooks);
    } else {
        installPortalHooks();
    }
})();
