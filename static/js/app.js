/* ====================================================
   HYPETEX LIMITED - SAAS PORTAL FRONTEND LOGIC
   ==================================================== */

let currentUser = null;
function getCurrentUser() {
    if (typeof currentUser !== 'undefined' && currentUser) return currentUser;
    if (typeof window !== 'undefined' && window.currentUser) return window.currentUser;
    if (typeof readCachedAuthUser === 'function') {
        const cached = readCachedAuthUser();
        if (cached) {
            currentUser = cached;
            return cached;
        }
    }
    return null;
}
let activeView = 'login';
let activeTicketId = null;
let adminChart = null;
let lastNotifications = [];
let notificationsOpen = false;
let accountMenuOpen = false;
let authCheckGeneration = 0;
let logoutInProgress = false;

const APPLE_UI_FONT = '"SF Pro Text", "SF Pro Display", "SF Pro", -apple-system, BlinkMacSystemFont, "Helvetica Neue", sans-serif';
const AUTH_USER_CACHE_KEY = 'brixen_portal_user_v1';
const LUCIDE_SRC = '/static/js/vendor/lucide.min.js?v=0.469.0';
const CHART_SRC = '/static/js/vendor/chart.umd.min.js?v=4.4.1';

function loadExternalScript(src) {
    return new Promise((resolve, reject) => {
        const existing = document.querySelector(`script[data-src="${src}"]`) || document.querySelector(`script[src="${src}"]`);
        if (existing) {
            if (existing.dataset.loaded === '1') return resolve();
            existing.addEventListener('load', () => resolve(), { once: true });
            existing.addEventListener('error', () => reject(new Error(`Failed ${src}`)), { once: true });
            return;
        }
        const script = document.createElement('script');
        script.src = src;
        script.async = true;
        script.dataset.src = src;
        script.onload = () => {
            script.dataset.loaded = '1';
            resolve();
        };
        script.onerror = () => reject(new Error(`Failed ${src}`));
        document.head.appendChild(script);
    });
}

function ensureLucideLoaded() {
    if (window.lucide && typeof window.lucide.createIcons === 'function') {
        return Promise.resolve();
    }
    if (!window.__lucideLoadPromise) {
        window.__lucideLoadPromise = loadExternalScript(LUCIDE_SRC).catch(() => {});
    }
    return window.__lucideLoadPromise;
}

function ensureChartJs() {
    if (typeof Chart === 'function') {
        if (Chart.defaults && Chart.defaults.font) Chart.defaults.font.family = APPLE_UI_FONT;
        return Promise.resolve();
    }
    if (!window.__chartLoadPromise) {
        window.__chartLoadPromise = loadExternalScript(CHART_SRC).then(() => {
            if (typeof Chart === 'function' && Chart.defaults && Chart.defaults.font) {
                Chart.defaults.font.family = APPLE_UI_FONT;
            }
        });
    }
    return window.__chartLoadPromise;
}

function readCachedAuthUser() {
    try {
        const raw = localStorage.getItem(AUTH_USER_CACHE_KEY) || sessionStorage.getItem(AUTH_USER_CACHE_KEY);
        if (!raw) return null;
        const user = JSON.parse(raw);
        return user && user.id ? user : null;
    } catch (_) {
        return null;
    }
}

function writeCachedAuthUser(user) {
    try {
        sessionStorage.removeItem(AUTH_USER_CACHE_KEY);
        if (user && user.id) localStorage.setItem(AUTH_USER_CACHE_KEY, JSON.stringify(user));
        else localStorage.removeItem(AUTH_USER_CACHE_KEY);
    } catch (_) { /* ignore quota */ }
}

const ALL_USER_ROLES = ['SUPER_ADMIN', 'ADMIN', 'MANAGER', 'STAFF', 'CLIENT'];
const ROLE_RANK = { CLIENT: 0, STAFF: 1, MANAGER: 2, ADMIN: 3, SUPER_ADMIN: 4 };
const STAFF_DEPARTMENTS = [
    'New Signups',
    'Orders',
    'Documents',
    'Support',
    'Compliance',
    'Accounts',
    'Accountancy',
    'General'
];

function isAdminShellUser(user) {
    if (!user) return false;
    return ['SUPER_ADMIN', 'ADMIN', 'MANAGER', 'STAFF'].includes(user.role);
}

function canViewAdminDashboard() {
    return currentUser && ['SUPER_ADMIN', 'ADMIN', 'MANAGER', 'STAFF'].includes(currentUser.role);
}

function canViewRevenue() {
    return currentUser && ['SUPER_ADMIN', 'ADMIN'].includes(currentUser.role);
}

function hasFullModuleAccess() {
    return currentUser && ['SUPER_ADMIN', 'ADMIN', 'MANAGER', 'STAFF'].includes(currentUser.role);
}

const VIEW_ACCESS = {
    'admin-customers': ['New Signups'],
    'admin-orders': ['Orders', 'Support'],
    'admin-services': ['Orders'],
    'admin-documents': ['Documents', 'Compliance'],
    'admin-intake': ['Documents', 'Compliance'],
    'admin-invoices': ['Accounts'],
    'admin-accountancy': ['Accountancy', 'Accounts', 'Compliance']
};

function hasModuleAccess(areas) {
    if (hasFullModuleAccess()) return true;
    if (!currentUser || currentUser.role !== 'STAFF') return false;
    const granted = staffDepartments(currentUser);
    return (areas || []).some((area) => granted.includes(area));
}

function canOpenView(viewName) {
    if (!viewName || viewName === 'login' || viewName === 'client-profile') return true;
    if (viewName === 'admin-dashboard') return canViewAdminDashboard();
    if (viewName === 'admin-invoices' && currentUser && currentUser.role === 'STAFF') return false;
    if (viewName === 'admin-logs' || viewName === 'admin-settings') {
        return canManageUsers();
    }
    if (viewName === 'admin-team') {
        // Legacy hash; combined into Tasks & Team.
        return canManageUsers() || isAdminShellUser(currentUser);
    }
    if (viewName === 'admin-tasks') return isAdminShellUser(currentUser);
    const needed = VIEW_ACCESS[viewName];
    if (needed) return hasModuleAccess(needed);
    if (String(viewName).startsWith('client-')) {
        return currentUser && !isAdminShellUser(currentUser);
    }
    return isAdminShellUser(currentUser);
}

function defaultPortalView() {
    if (!isAdminShellUser(currentUser)) return 'client-dashboard';
    if (canViewAdminDashboard()) return 'admin-dashboard';
    const preferred = ['admin-orders', 'admin-customers', 'admin-documents', 'admin-invoices', 'admin-accountancy', 'admin-tasks', 'client-profile'];
    return preferred.find(canOpenView) || 'client-profile';
}

const VIEW_HASH = {
    'client-dashboard': 'dashboard',
    'client-profile': 'profile',
    'client-companies': 'companies',
    'client-accountancy': 'accountancy',
    'client-addresses': 'addresses',
    'client-orders': 'orders',
    'client-invoices': 'invoices',
    'client-payments': 'payments',
    'client-documents': 'documents',
    'client-services': 'services',
    'client-support': 'support',
    'client-messages': 'messages',
    'admin-dashboard': 'admin-dashboard',
    'admin-customers': 'admin-customers',
    'admin-companies': 'admin-companies',
    'admin-accountancy': 'admin-accountancy',
    'admin-orders': 'admin-orders',
    'admin-tasks': 'admin-tasks',
    'admin-services': 'admin-services',
    'admin-invoices': 'admin-invoices',
    'admin-documents': 'admin-documents',
    'admin-logs': 'admin-logs',
    'admin-settings': 'admin-settings',
};

const HASH_VIEW = {
    'admin-team': 'admin-tasks',
    'admin-quick-tasks': 'admin-tasks',
};
Object.keys(VIEW_HASH).forEach((view) => {
    HASH_VIEW[VIEW_HASH[view]] = view;
    HASH_VIEW[view] = view;
});

function viewFromHash(raw) {
    const hash = String(raw || '').replace(/^#/, '').split(/[/?]/)[0].trim();
    if (!hash || hash === 'login') return null;
    return HASH_VIEW[hash] || null;
}

function syncViewHash(viewName) {
    if (!viewName || viewName === 'login') return;
    const next = '#' + (VIEW_HASH[viewName] || viewName);
    if (location.hash === next) return;
    history.replaceState(null, '', location.pathname + location.search + next);
}

function initialPortalView() {
    const fromHash = viewFromHash(location.hash);
    if (fromHash && canOpenView(fromHash)) return fromHash;
    return defaultPortalView();
}

function applyPortalAccessNav() {
    document.querySelectorAll('#admin-sidebar .nav-item, #top-menu-list-admin .top-menu-item').forEach((el) => {
        const view = el.getAttribute('data-view');
        el.style.display = canOpenView(view) ? '' : 'none';
    });
    applyAdminRevenueCards();
    applyAdminOrderFinanceVisibility();
    const crmInvoices = document.getElementById('crm-tab-invoices');
    if (crmInvoices) {
        crmInvoices.style.display = (currentUser && currentUser.role === 'STAFF') ? 'none' : '';
    }
}

let detachedRevenueCards = [];

function applyAdminRevenueCards() {
    if (canViewRevenue()) {
        detachedRevenueCards.forEach(({ parent, html, next }) => {
            if (!parent || parent.querySelector('[data-admin-revenue]')) return;
            const wrap = document.createElement('div');
            wrap.innerHTML = html.trim();
            const node = wrap.firstElementChild;
            if (!node) return;
            node.hidden = false;
            if (next && next.parentNode === parent) parent.insertBefore(node, next);
            else parent.appendChild(node);
        });
        detachedRevenueCards = [];
        document.querySelectorAll('[data-admin-revenue]').forEach((el) => {
            el.hidden = false;
        });
        return;
    }
    document.querySelectorAll('[data-admin-revenue]').forEach((el) => {
        detachedRevenueCards.push({
            parent: el.parentElement,
            html: el.outerHTML,
            next: el.nextElementSibling
        });
        el.remove();
    });
}

function applyAdminOrderFinanceVisibility() {
    const hide = !canViewRevenue();
    document.querySelectorAll('.orders-table-wrap, .invoices-table-wrap, #modal-staff-order, #modal-edit-invoice').forEach((el) => {
        el.classList.toggle('hide-order-finance', hide);
    });
    const paymentFilter = document.getElementById('filter-admin-order-payment');
    if (paymentFilter) {
        paymentFilter.hidden = hide;
        if (hide) paymentFilter.value = '';
    }
}

function creatableRolesFor(user) {
    if (!user) return [];
    const hasStaffManage = ['SUPER_ADMIN', 'ADMIN'].includes(user.role);
    const hasClientsCreate = ['SUPER_ADMIN', 'ADMIN'].includes(user.role);
    if (!hasStaffManage && !hasClientsCreate) return [];
    const actorRank = ROLE_RANK[user.role];
    if (actorRank == null) return [];
    return ALL_USER_ROLES.filter((role) => {
        if (role === 'CLIENT' && !hasClientsCreate) return false;
        if (role !== 'CLIENT' && !hasStaffManage) return false;
        if (user.role === 'SUPER_ADMIN') return true;
        return ROLE_RANK[role] < actorRank;
    });
}

function canManageServiceCatalog() {
    return currentUser && ['SUPER_ADMIN', 'ADMIN'].includes(currentUser.role);
}

function canManageUsers() {
    return creatableRolesFor(currentUser).length > 0;
}

function canManageStaff() {
    return canManageUsers();
}

function setAuthShellState(pending, authenticated) {
    const root = document.documentElement;
    const app = document.getElementById('app-container');
    if (pending) {
        root.classList.add('auth-pending');
        root.classList.remove('authenticated', 'auth-ready');
        if (app) app.setAttribute('hidden', '');
        return;
    }
    root.classList.remove('auth-pending');
    root.classList.add('auth-ready');
    if (authenticated) root.classList.add('authenticated');
    else root.classList.remove('authenticated');
    if (app) app.removeAttribute('hidden');
}

function setActiveViewPanel(viewName) {
    document.querySelectorAll('.view-panel').forEach((panel) => {
        panel.classList.remove('is-active');
        panel.style.display = 'none';
    });
    const targetPanel = document.getElementById(viewName ? `view-${viewName}` : '');
    if (targetPanel) {
        targetPanel.classList.add('is-active');
        targetPanel.style.display = viewName === 'login' ? 'block' : 'flex';
    }
}

function setOpenSidebar(which) {
    const clientSide = document.getElementById('client-sidebar');
    const adminSide = document.getElementById('admin-sidebar');
    if (clientSide) {
        const open = which === 'client';
        clientSide.classList.toggle('is-open', open);
        clientSide.style.display = open ? 'flex' : 'none';
        if (open) clientSide.removeAttribute('hidden');
        else clientSide.setAttribute('hidden', '');
    }
    if (adminSide) {
        const open = which === 'admin';
        adminSide.classList.toggle('is-open', open);
        adminSide.style.display = open ? 'flex' : 'none';
        if (open) adminSide.removeAttribute('hidden');
        else adminSide.setAttribute('hidden', '');
    }
    if (!which) closeMobileNav();
}

function isMobileNav() {
    return window.matchMedia('(max-width: 900px)').matches;
}

function syncMobileNavUi() {
    const open = document.body.classList.contains('mobile-nav-open');
    const btn = document.getElementById('mobile-nav-toggle');
    const backdrop = document.getElementById('mobile-nav-backdrop');
    if (btn) {
        btn.setAttribute('aria-expanded', open ? 'true' : 'false');
        btn.setAttribute('aria-label', open ? 'Close menu' : 'Open menu');
    }
    if (backdrop) {
        if (open) backdrop.removeAttribute('hidden');
        else backdrop.setAttribute('hidden', '');
    }
    safeCreateIcons();
}

function toggleMobileNav() {
    if (!isMobileNav()) return;
    const opening = !document.body.classList.contains('mobile-nav-open');
    document.body.classList.toggle('mobile-nav-open', opening);
    document.querySelectorAll('.sidebar.is-open').forEach((side) => {
        side.classList.toggle('mobile-open', opening);
    });
    syncMobileNavUi();
}

function closeMobileNav() {
    document.body.classList.remove('mobile-nav-open');
    document.querySelectorAll('.sidebar').forEach((side) => side.classList.remove('mobile-open'));
    syncMobileNavUi();
}

function syncCreateUserButtons() {
    const show = canManageUsers();
    document.querySelectorAll('#btn-create-user, .btn-create-user').forEach((btn) => {
        btn.style.display = show ? 'inline-flex' : 'none';
    });
    syncTasksTeamChrome();
}

function safeCreateIcons() {
    ensureLucideLoaded().then(() => {
        try {
            if (window.lucide && typeof lucide.createIcons === 'function') lucide.createIcons();
        } catch (err) {
            console.warn('Icon render skipped', err);
        }
    });
}

// Prefer instant shell from session cache; auth check confirms in background.
(function bootAuthShellHint() {
    const cached = readCachedAuthUser();
    if (cached) {
        currentUser = cached;
        setAuthShellState(false, true);
    } else {
        setAuthShellState(true, false);
    }
})();

let currentTheme = localStorage.getItem('brixen_theme') || (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');

function initThemeSystem() {
    applyThemeUI(currentTheme);
    if (window.matchMedia) {
        window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', (e) => {
            if (!localStorage.getItem('brixen_theme')) {
                setTheme(e.matches ? 'dark' : 'light', false);
            }
        });
    }
}

function applyThemeUI(theme) {
    currentTheme = theme;
    document.documentElement.setAttribute('data-theme', theme);
    const btn = document.getElementById('theme-toggle-btn');
    const icon = document.getElementById('theme-toggle-icon');
    const text = document.getElementById('theme-toggle-text');
    
    if (theme === 'dark') {
        if (text) text.textContent = 'Dark';
        if (icon) icon.setAttribute('data-lucide', 'moon');
        if (btn) btn.setAttribute('title', 'Switch to Light mode (currently Dark)');
    } else {
        if (text) text.textContent = 'Light';
        if (icon) icon.setAttribute('data-lucide', 'sun');
        if (btn) btn.setAttribute('title', 'Switch to Dark mode (currently Light)');
    }
    requestAnimationFrame(() => safeCreateIcons());
    updateChartsTheme(theme);
}

async function setTheme(theme, save = true) {
    if (!['light', 'dark'].includes(theme)) return;
    applyThemeUI(theme);
    if (save) {
        localStorage.setItem('brixen_theme', theme);
        if (currentUser) {
            try {
                fetch('/api/user/preferences', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    credentials: 'same-origin',
                    body: JSON.stringify({ theme: theme })
                }).catch(() => {});
            } catch (e) {}
        }
    }
}

function toggleTheme() {
    const nextTheme = currentTheme === 'dark' ? 'light' : 'dark';
    setTheme(nextTheme, true);
}

function updateChartsTheme(theme) {
    if (typeof Chart === 'undefined') return;
    const isDark = theme === 'dark';
    const gridColor = isDark ? 'rgba(255, 255, 255, 0.08)' : 'rgba(0, 0, 0, 0.06)';
    const textColor = isDark ? '#94a3b8' : '#64748b';

    try {
        Chart.defaults.color = textColor;
        if (Chart.defaults.scale && Chart.defaults.scale.grid) {
            Chart.defaults.scale.grid.color = gridColor;
        }
        if (Chart.instances) {
            Object.keys(Chart.instances).forEach(key => {
                const chart = Chart.instances[key];
                if (!chart || !chart.options) return;
                if (chart.options.scales) {
                    Object.keys(chart.options.scales).forEach(scaleKey => {
                        const scale = chart.options.scales[scaleKey];
                        if (scale.grid) scale.grid.color = gridColor;
                        if (scale.ticks) scale.ticks.color = textColor;
                    });
                }
                if (chart.options.plugins && chart.options.plugins.legend && chart.options.plugins.legend.labels) {
                    chart.options.plugins.legend.labels.color = textColor;
                }
                chart.update('none');
            });
        }
    } catch (err) {}
}

// Initialize Application on Page Load
document.addEventListener('DOMContentLoaded', async () => {
    initThemeSystem();
    try {
        await checkAuth();
        if (currentUser && currentUser.theme_preference && ['light', 'dark'].includes(currentUser.theme_preference)) {
            if (!localStorage.getItem('brixen_theme')) {
                setTheme(currentUser.theme_preference, false);
            }
        }
    } catch (err) {
        console.error('Auth check error:', err);
        // Keep any cached session on boot errors — never force logout on refresh.
        if (!readCachedAuthUser()) {
            currentUser = null;
            showLoginView();
        }
    }
    setupEventListeners();
    requestAnimationFrame(() => safeCreateIcons());
    window.addEventListener('hashchange', () => {
        if (!currentUser) return;
        const view = viewFromHash(location.hash);
        if (view && view !== activeView && canOpenView(view)) switchView(view);
    });
    window.addEventListener('resize', () => {
        if (!isMobileNav()) closeMobileNav();
    });
});

window.addEventListener('pageshow', (event) => {
    if (!event.persisted) return;
    checkAuth();
});

function setupEventListeners() {
    // Nav Click Listeners (sidebar + top menu)
    document.querySelectorAll('.nav-item, .top-menu-item').forEach(item => {
        item.addEventListener('click', (e) => {
            e.preventDefault();
            const view = item.getAttribute('data-view');
            if (view) switchView(view);
        });
    });
    document.addEventListener('click', (e) => {
        const wrap = document.getElementById('notification-bell-wrap');
        if (notificationsOpen && wrap && !wrap.contains(e.target)) {
            closeNotificationsDropdown();
        }
        const accountWrap = document.getElementById('header-account-wrap');
        if (accountMenuOpen && accountWrap && !accountWrap.contains(e.target)) {
            closeAccountMenu();
        }
        const orderAction = e.target.closest('[data-order-action]');
        if (orderAction) {
            const action = orderAction.getAttribute('data-order-action');
            if (action === 'status' || action === 'payment_mode' || action === 'total') return;
            e.preventDefault();
            e.stopPropagation();
            const orderId = Number(orderAction.getAttribute('data-order-id'));
            if (!orderId) return;
            if (action === 'open') openStaffOrderWorkspace(orderId);
            if (action === 'change-details') openStaffOrderWorkspace(orderId, { editCheckout: true });
            if (action === 'progress') advanceOrderProgress(orderId, Number(orderAction.getAttribute('data-progress') || 0));
            if (action === 'delete') openDeleteOrderModal(orderId, orderAction.getAttribute('data-order-number'));
            if (action === 'edit-price') beginAdminOrderPriceEdit(orderId, orderAction.closest('.table-price-wrap'));
            return;
        }
        const customerAction = e.target.closest('[data-customer-action]');
        if (customerAction) {
            e.preventDefault();
            e.stopPropagation();
            const customerId = Number(customerAction.getAttribute('data-customer-id'));
            if (!customerId) return;
            const action = customerAction.getAttribute('data-customer-action');
            if (action === 'profile') openCrmClientModal(customerId);
            if (action === 'password') {
                setClientPortalPassword(customerId, customerAction.getAttribute('data-customer-email') || '');
            }
            return;
        }
        const accountancyAction = e.target.closest('[data-accountancy-action]');
        if (accountancyAction) {
            e.preventDefault();
            e.stopPropagation();
            const companyId = Number(accountancyAction.getAttribute('data-accountancy-id'));
            if (!companyId) return;
            const action = accountancyAction.getAttribute('data-accountancy-action');
            if (action === 'books') openAccountancyBooks(companyId);
            if (action === 'file') openFileAccountsModal(companyId);
            return;
        }
        const orderRow = e.target.closest('#adm-orders-table-body tr[data-order-id]');
        if (orderRow && !e.target.closest('select, a, button, input, textarea, .icon-edit-btn, .table-price-wrap')) {
            const orderId = Number(orderRow.getAttribute('data-order-id'));
            if (orderId) openStaffOrderWorkspace(orderId);
        }
    });
    const ordersBody = document.getElementById('adm-orders-table-body');
    if (ordersBody) {
        ordersBody.addEventListener('change', (e) => {
            const rowCheck = e.target.closest('input.adm-order-select');
            if (rowCheck) {
                syncAdminOrdersSelectAllState();
                return;
            }
            const statusSel = e.target.closest('select[data-order-action="status"]');
            if (statusSel) {
                const orderId = Number(statusSel.getAttribute('data-order-id'));
                if (orderId) updateAdminOrderStatus(orderId, statusSel.value);
                return;
            }
            const modeSel = e.target.closest('select[data-order-action="payment_mode"]');
            if (modeSel) {
                const orderId = Number(modeSel.getAttribute('data-order-id'));
                if (orderId) updateAdminOrderPaymentMode(orderId, modeSel.value);
                return;
            }
            const priceInput = e.target.closest('input[data-order-action="total"]');
            if (priceInput) {
                const orderId = Number(priceInput.getAttribute('data-order-id'));
                if (orderId) updateAdminOrderTotal(orderId, priceInput.value, priceInput);
            }
        });
        ordersBody.addEventListener('keydown', (e) => {
            const priceInput = e.target.closest('input[data-order-action="total"]');
            if (!priceInput) return;
            if (e.key === 'Enter') {
                e.preventDefault();
                priceInput.blur();
            }
            if (e.key === 'Escape') {
                e.preventDefault();
                loadAdminOrders();
            }
        });
    }
}

// ----------------------------------------------------
// Authentication & Production Login Flow
// ----------------------------------------------------
async function checkAuth() {
    const generation = ++authCheckGeneration;
    const cached = readCachedAuthUser();
    if (cached && cached.id) {
        currentUser = cached;
        updateUserUI();
        switchView(initialPortalView(), { force: true });
        setAuthShellState(false, true);
    } else {
        setAuthShellState(true, false);
    }
    try {
        const res = await fetch('/api/auth/me', { credentials: 'same-origin' });
        if (generation !== authCheckGeneration) return;
        const data = await res.json().catch(() => ({}));
        if (generation !== authCheckGeneration) return;
        if (res.ok && data.status === 'success' && data.user) {
            currentUser = data.user;
            writeCachedAuthUser(data.user);
            updateUserUI();
            switchView(initialPortalView(), { force: true });
            setAuthShellState(false, true);
            return;
        }
        // Only clear session on a confirmed unauthenticated response with no usable cache recovery.
        if (res.status === 401) {
            currentUser = null;
            writeCachedAuthUser(null);
            showLoginView();
        }
        // Any other failure: keep cached signed-in state (refresh / server blip).
    } catch (err) {
        console.error('Auth check error:', err);
        if (generation !== authCheckGeneration) return;
        if (!cached) {
            currentUser = null;
            showLoginView();
        }
    }
}

function setHeaderAuthVisible(visible) {
    const searchBox = document.getElementById('header-search-box');
    const accountWrap = document.getElementById('header-account-wrap');
    const bellWrap = document.getElementById('notification-bell-wrap');
    if (searchBox) searchBox.style.display = visible ? '' : 'none';
    if (accountWrap) accountWrap.style.display = visible ? 'block' : 'none';
    if (bellWrap) bellWrap.style.display = visible ? 'block' : 'none';
    if (!visible) closeAccountMenu();
}

function clearLoggedInChrome() {
    const emptyIds = [
        'current-user-avatar', 'current-user-name', 'current-user-role',
        'admin-user-avatar', 'admin-user-name', 'admin-user-role',
        'header-user-avatar', 'header-user-name', 'header-user-role'
    ];
    emptyIds.forEach((id) => {
        const el = document.getElementById(id);
        if (el) el.textContent = '';
    });
    const unread = document.getElementById('header-unread-count');
    if (unread) {
        unread.textContent = '0';
        unread.style.display = 'none';
    }
    const notesList = document.getElementById('notifications-list');
    if (notesList) notesList.innerHTML = '';
    lastNotifications = [];
    resetClientDashboardView();
}

function showLoginView() {
    currentUser = null;
    writeCachedAuthUser(null);
    activeView = 'login';
    closeAccountMenu();
    closeNotificationsDropdown();
    if (typeof closeCrmClientModal === 'function') {
        closeCrmClientModal();
    }
    if (typeof closeCreateUserModal === 'function') {
        closeCreateUserModal();
    }

    setOpenSidebar(null);
    setHeaderAuthVisible(false);
    if (typeof hideGlobalSearchResults === 'function') hideGlobalSearchResults();
    clearLoggedInChrome();
    syncCreateUserButtons();
    const topMenuBar = document.getElementById('top-horizontal-menu-bar');
    if (topMenuBar) topMenuBar.style.display = 'none';
    setActiveViewPanel('login');
    setTeamMessage('', '');

    const passInput = document.getElementById('login-password');
    if (passInput) passInput.value = '';
    setAuthShellState(false, false);
}

async function handleFormLogin(e) {
    if (e) e.preventDefault();
    const emailInput = document.getElementById('login-email');
    const passInput = document.getElementById('login-password');
    const errDiv = document.getElementById('login-error-msg');
    const submitBtn = e && e.target && e.target.querySelector
        ? e.target.querySelector('button[type="submit"]')
        : document.querySelector('#login-form button[type="submit"]');
    
    if (!emailInput || !passInput) return;
    const email = emailInput.value;
    const password = passInput.value;
    if (errDiv) errDiv.style.display = 'none';
    if (submitBtn) submitBtn.disabled = true;

    // Invalidate any in-flight /api/auth/me so a stale 401 cannot wipe this login.
    const generation = ++authCheckGeneration;

    try {
        const res = await fetch('/api/auth/login', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            credentials: 'same-origin',
            body: JSON.stringify({ email, password })
        });
        const data = await res.json();
        if (generation !== authCheckGeneration) return;
        if (data.status === 'success' && data.user) {
            currentUser = data.user;
            writeCachedAuthUser(data.user);
            setAuthShellState(false, true);
            updateUserUI();
            // Force dashboard open — activeView may still look like a prior page after a soft logout.
            switchView(defaultPortalView(), { force: true });
        } else {
            if (errDiv) {
                errDiv.textContent = data.message || 'Login failed.';
                errDiv.style.display = 'block';
            }
        }
    } catch (err) {
        if (errDiv) {
            errDiv.textContent = 'Server communication error.';
            errDiv.style.display = 'block';
        }
    } finally {
        if (submitBtn) submitBtn.disabled = false;
    }
}

async function handleLogout() {
    if (logoutInProgress) return;
    logoutInProgress = true;
    currentUser = null;
    closeAccountMenu();
    closeNotificationsDropdown();
    showLoginView();
    try {
        await fetch('/api/auth/logout', { method: 'POST', credentials: 'same-origin' });
    } catch (_) { /* ignore */ }
    logoutInProgress = false;
}

function closeAccountMenu() {
    accountMenuOpen = false;
    const menu = document.getElementById('account-dropdown');
    if (menu) menu.style.display = 'none';
}

async function toggleAccountMenu(event) {
    if (event) event.stopPropagation();
    if (!currentUser) return;
    closeNotificationsDropdown();
    const menu = document.getElementById('account-dropdown');
    if (!menu) return;
    if (accountMenuOpen) {
        closeAccountMenu();
        return;
    }
    await refreshAccountSwitcher();
    accountMenuOpen = true;
    menu.style.display = 'block';
}

function canImpersonateTarget(target) {
    if (!currentUser || !target || currentUser.impersonated) return false;
    if (!canManageUsers()) return false;
    if (!isAdminShellUser(target)) return false;
    if (currentUser.id != null && String(currentUser.id) === String(target.id)) return false;
    if (target.status && target.status !== 'Active') return false;
    if (currentUser.role === 'SUPER_ADMIN') return true;
    return (ROLE_RANK[target.role] || 99) < (ROLE_RANK[currentUser.role] || 0);
}

async function refreshAccountSwitcher() {
    const returnBtn = document.getElementById('account-menu-return');
    if (returnBtn) {
        returnBtn.style.display = currentUser && currentUser.impersonated ? 'flex' : 'none';
    }
    const wrap = document.getElementById('account-user-switcher');
    const list = document.getElementById('account-user-switch-list');
    if (!wrap || !list) return;
    if (!canManageUsers() || (currentUser && currentUser.impersonated)) {
        wrap.style.display = 'none';
        list.innerHTML = '';
        return;
    }
    wrap.style.display = 'block';
    list.innerHTML = '<p style="padding:8px 12px; font-size:0.75rem; color:#94a3b8;">Loading users…</p>';
    try {
        const staffRes = await fetch('/api/admin/staff?include_inactive=1', { credentials: 'same-origin' });
        const staffData = await staffRes.json();
        const merged = [];
        const seen = new Set();
        (staffData.staff || []).forEach((u) => {
            if (!u || u.id == null || seen.has(u.id)) return;
            seen.add(u.id);
            merged.push(u);
        });
        const switchable = merged.filter(canImpersonateTarget)
            .sort((a, b) => String(a.full_name || '').localeCompare(String(b.full_name || '')));
        if (!switchable.length) {
            list.innerHTML = '<p style="padding:8px 12px; font-size:0.75rem; color:#94a3b8;">No other users available.</p>';
            return;
        }
        list.innerHTML = switchable.map((u) => `
            <button type="button" class="account-user-option" onclick="switchToUser(${u.id}, event)">
                <span class="account-user-name">${escapeHtml(u.full_name)}</span>
                <span class="account-user-meta">${escapeHtml(u.role)} · ${escapeHtml(u.email)}</span>
            </button>
        `).join('');
    } catch (err) {
        list.innerHTML = '<p style="padding:8px 12px; font-size:0.75rem; color:#dc2626;">Unable to load users.</p>';
    }
}

async function switchToUser(userId, event) {
    if (event) event.stopPropagation();
    if (!canManageUsers()) return;
    try {
        const res = await fetch('/api/admin/impersonate', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            credentials: 'same-origin',
            body: JSON.stringify({ user_id: userId })
        });
        const data = await res.json();
        if (!res.ok || data.status !== 'success' || !data.user) {
            throw new Error(data.message || 'Unable to switch user.');
        }
        currentUser = data.user;
        writeCachedAuthUser(data.user);
        closeAccountMenu();
        resetClientDashboardView();
        updateUserUI();
        switchView(defaultPortalView());
        setAuthShellState(false, true);
    } catch (err) {
        alert(err.message || 'Unable to switch user.');
    }
}

async function stopImpersonation(event) {
    if (event) event.stopPropagation();
    try {
        const res = await fetch('/api/admin/impersonate/stop', {
            method: 'POST',
            credentials: 'same-origin'
        });
        const data = await res.json();
        if (!res.ok || data.status !== 'success' || !data.user) {
            throw new Error(data.message || 'Unable to return to original account.');
        }
        currentUser = data.user;
        writeCachedAuthUser(data.user);
        closeAccountMenu();
        resetClientDashboardView();
        updateUserUI();
        switchView(defaultPortalView());
        setAuthShellState(false, true);
    } catch (err) {
        alert(err.message || 'Unable to return to original account.');
    }
}

function openAccountProfile(event) {
    if (event) event.stopPropagation();
    closeAccountMenu();
    if (!currentUser) return;
    switchView('client-profile');
}

function setProfileNotice(kind, message) {
    const err = document.getElementById('profile-error');
    const ok = document.getElementById('profile-success');
    if (err) {
        err.style.display = kind === 'error' && message ? 'block' : 'none';
        err.textContent = kind === 'error' ? (message || '') : '';
    }
    if (ok) {
        ok.style.display = kind === 'success' && message ? 'block' : 'none';
        ok.textContent = kind === 'success' ? (message || '') : '';
    }
}

function loadUserProfile() {
    if (!currentUser) return;
    setProfileNotice('', '');
    const email = document.getElementById('prof-email');
    const role = document.getElementById('prof-role');
    const name = document.getElementById('prof-fullname');
    const phone = document.getElementById('prof-phone');
    const country = document.getElementById('prof-country');
    const address = document.getElementById('prof-address');
    if (email) email.value = currentUser.email || '';
    if (role) role.value = currentUser.role || '';
    if (name) name.value = currentUser.full_name || '';
    if (phone) phone.value = currentUser.phone || '';
    if (country) country.value = currentUser.country || '';
    if (address) address.value = currentUser.address || '';
    ['prof-current-password', 'prof-new-password', 'prof-confirm-password'].forEach((id) => {
        const el = document.getElementById(id);
        if (el) el.value = '';
    });
    const pwBlock = document.getElementById('profile-password-block');
    if (pwBlock) pwBlock.style.display = currentUser.impersonated ? 'none' : 'block';
}

async function saveProfile(event) {
    event.preventDefault();
    if (!currentUser) return;
    setProfileNotice('', '');
    const currentPassword = document.getElementById('prof-current-password')?.value || '';
    const newPassword = document.getElementById('prof-new-password')?.value || '';
    const confirmPassword = document.getElementById('prof-confirm-password')?.value || '';
    if (newPassword || confirmPassword || currentPassword) {
        if (currentUser.impersonated) {
            setProfileNotice('error', 'Return to your own account to change the password.');
            return;
        }
        if (!currentPassword) {
            setProfileNotice('error', 'Enter your current password to change it.');
            return;
        }
        if (newPassword.length < 10) {
            setProfileNotice('error', 'New password must be at least 10 characters.');
            return;
        }
        if (newPassword !== confirmPassword) {
            setProfileNotice('error', 'New passwords do not match.');
            return;
        }
    }
    const payload = {
        full_name: document.getElementById('prof-fullname')?.value.trim() || '',
        phone: document.getElementById('prof-phone')?.value.trim() || '',
        country: document.getElementById('prof-country')?.value.trim() || '',
        address: document.getElementById('prof-address')?.value.trim() || ''
    };
    if (newPassword) {
        payload.current_password = currentPassword;
        payload.new_password = newPassword;
        payload.confirm_password = confirmPassword;
    }
    const btn = document.getElementById('profile-save-btn');
    if (btn) btn.disabled = true;
    try {
        const res = await fetch('/api/auth/profile', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            credentials: 'same-origin',
            body: JSON.stringify(payload)
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data.status !== 'success') {
            throw new Error(data.message || 'Unable to save profile.');
        }
        if (data.user) {
            currentUser = { ...currentUser, ...data.user };
            updateUserUI();
        }
        loadUserProfile();
        setProfileNotice('success', data.message || 'Profile updated.');
    } catch (err) {
        setProfileNotice('error', err.message || 'Unable to save profile.');
    } finally {
        if (btn) btn.disabled = false;
    }
}

function updateUserUI() {
    if (!currentUser) return;
    
    // Update Sidebar User Info
    const avatar = document.getElementById('current-user-avatar');
    const nameEl = document.getElementById('current-user-name');
    const roleEl = document.getElementById('current-user-role');
    const dashWelcome = document.getElementById('dash-welcome-text');
    
    if (nameEl) nameEl.textContent = currentUser.full_name;
    if (roleEl) roleEl.textContent = `${currentUser.role} • Active`;
    if (dashWelcome) dashWelcome.textContent = `Welcome back, ${currentUser.full_name}`;
    
    if (avatar) {
        const initials = currentUser.full_name.split(' ').map(n => n[0]).join('').substring(0,2);
        avatar.textContent = initials;
    }
    
    // Update Glass Header Account Info
    const headerAvatar = document.getElementById('header-user-avatar');
    const headerName = document.getElementById('header-user-name');
    const headerRole = document.getElementById('header-user-role');
    
    if (headerName) headerName.textContent = currentUser.full_name;
    if (headerRole) headerRole.textContent = isAdminShellUser(currentUser) ? 'Admin CMS View' : 'Client Account';
    if (headerAvatar) {
        const initials = currentUser.full_name.split(' ').map(n => n[0]).join('').substring(0,2);
        headerAvatar.textContent = initials;
    }

    setHeaderAuthVisible(true);

    const adminName = document.getElementById('admin-user-name');
    const adminRole = document.getElementById('admin-user-role');
    const adminAvatar = document.getElementById('admin-user-avatar');
    if (adminName) adminName.textContent = currentUser.full_name;
    if (adminRole) adminRole.textContent = currentUser.role;
    if (adminAvatar) {
        adminAvatar.textContent = currentUser.full_name.split(' ').map(n => n[0]).join('').substring(0,2);
    }
    
    if (isAdminShellUser(currentUser)) {
        setOpenSidebar('admin');
    } else {
        setOpenSidebar('client');
    }
    const profileItem = document.getElementById('account-menu-profile');
    if (profileItem) {
        profileItem.style.display = 'flex';
        profileItem.textContent = 'Profile & Password';
    }
    const returnBtn = document.getElementById('account-menu-return');
    if (returnBtn) {
        returnBtn.style.display = currentUser.impersonated ? 'flex' : 'none';
    }
    const switcher = document.getElementById('account-user-switcher');
    if (switcher && currentUser.impersonated) switcher.style.display = 'none';
    applyPortalAccessNav();
    syncCreateUserButtons();
    
    const topMenuBar = document.getElementById('top-horizontal-menu-bar');
    if (topMenuBar) topMenuBar.style.display = 'none';

    loadNotificationsCount();
}

// ----------------------------------------------------
// View Router & Page Switching
// ----------------------------------------------------
function capturePageScroll() {
    const main = document.querySelector('.main-wrapper');
    return {
        x: window.scrollX || window.pageXOffset || 0,
        y: window.scrollY || window.pageYOffset || 0,
        mainX: main ? main.scrollLeft : 0,
        mainY: main ? main.scrollTop : 0,
    };
}

function restorePageScroll(pos) {
    if (!pos) return;
    const apply = () => {
        window.scrollTo(pos.x, pos.y);
        const main = document.querySelector('.main-wrapper');
        if (main) {
            main.scrollLeft = pos.mainX;
            main.scrollTop = pos.mainY;
        }
    };
    apply();
    requestAnimationFrame(() => {
        apply();
        requestAnimationFrame(apply);
    });
}

function switchView(viewName, options) {
    const opts = options || {};
    closeAccountMenu();
    closeMobileNav();
    let tasksTeamTab = null;
    let openQuickTasks = false;
    if (viewName === 'admin-team') {
        viewName = 'admin-tasks';
        tasksTeamTab = 'team';
    }
    if (viewName === 'admin-quick-tasks') {
        viewName = 'admin-tasks';
        tasksTeamTab = 'tasks';
        openQuickTasks = true;
    }
    if (currentUser && viewName !== 'login' && !canOpenView(viewName)) {
        viewName = defaultPortalView();
    }
    if (!currentUser && viewName !== 'login') {
        showLoginView();
        return;
    }
    // Same page click: stay put — but never skip leaving the login screen.
    if (!opts.force && viewName === activeView && viewName !== 'login') {
        syncViewHash(viewName);
        return;
    }
    if (viewName === 'login') {
        showLoginView();
        return;
    }
    const scrollPos = capturePageScroll();
    activeView = viewName;
    setActiveViewPanel(viewName);
    syncViewHash(viewName);
    
    // Deactivate nav items
    document.querySelectorAll('.nav-item').forEach(item => {
        item.classList.remove('active');
    });
    
    // Activate nav item
    const targetNavItem = document.querySelector(`.nav-item[data-view="${viewName}"]`);
    if (targetNavItem) {
        targetNavItem.classList.add('active');
    }

    // Sync top horizontal menu items
    document.querySelectorAll('.top-menu-item').forEach(item => {
        item.classList.toggle('active', item.getAttribute('data-view') === viewName);
    });
    
    // Execute view data loader
    switch(viewName) {
        case 'client-orders': loadClientOrders(); break;
        case 'client-dashboard': loadClientDashboard(); break;
        case 'client-companies': loadClientCompanies(); break;
        case 'client-accountancy': loadClientAccountancy(); break;
        case 'client-addresses': loadClientAddresses(); break;
        case 'client-invoices': loadClientInvoices(); break;
        case 'client-payments': loadClientPayments(); break;
        case 'client-documents': loadClientDocuments(); break;
        case 'client-services': loadClientServices(); break;
        case 'client-support': loadClientTickets(); break;
        case 'client-messages': loadClientMessages(); break;
        case 'client-profile': loadUserProfile(); break;
        
        case 'admin-dashboard': loadAdminDashboard(); break;
        case 'admin-orders': loadAdminOrders(); break;
        case 'admin-tasks':
            if (tasksTeamTab === 'team' && canManageUsers()) switchTasksTeamTab('team');
            else switchTasksTeamTab('tasks');
            if (openQuickTasks) setTimeout(() => {
                const el = document.getElementById('quick-tasks-page');
                if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
            }, 0);
            break;
        case 'admin-customers': loadAdminCustomers(); break;
        case 'admin-companies': loadAdminCompanies(); break;
        case 'admin-accountancy': loadAdminAccountancy(); break;
        case 'admin-services': loadAdminServices(); break;
        case 'admin-invoices': loadAdminInvoices(); break;
        case 'admin-documents': loadAdminDocuments(); break;
        case 'admin-intake': loadSmartIntakeView(); break;
        case 'admin-incentives': loadTeamIncentivesReport(); break;
        case 'staff-dashboard': loadStaffDashboard(); break;
        case 'admin-logs': loadAdminLogs(); break;
        case 'admin-settings': loadAdminSettings(); break;
    }
    
    lucide.createIcons();
    if (!opts.resetScroll) restorePageScroll(scrollPos);
}

// ----------------------------------------------------
// CLIENT ORDERS VIEW (Matching Reference Screenshot)
// ----------------------------------------------------
let clientOrdersPage = 1;

function resetClientOrdersPage() {
    clientOrdersPage = 1;
}

function showClientOrdersError(message) {
    const box = document.getElementById('client-orders-error');
    if (!box) return;
    if (!message) {
        box.style.display = 'none';
        box.textContent = '';
        return;
    }
    box.textContent = message;
    box.style.display = 'block';
}

function renderClientOrdersSkeleton() {
    const container = document.getElementById('orders-cards-container');
    if (!container) return;
    container.innerHTML = [0, 1, 2].map(() => `
        <div class="history-order-card" aria-hidden="true" style="pointer-events:none; opacity:0.65;">
            <div class="history-order-top">
                <div class="history-order-id">
                    <div class="history-order-icon" style="background:#e2e8f0;"></div>
                    <div>
                        <div style="width:140px;height:16px;background:#e2e8f0;border-radius:4px;"></div>
                        <div style="width:88px;height:12px;background:#e2e8f0;border-radius:4px;margin-top:8px;"></div>
                    </div>
                </div>
                <div style="width:72px;height:16px;background:#e2e8f0;border-radius:4px;"></div>
            </div>
            <div style="height:18px;background:#e2e8f0;border-radius:6px;"></div>
            <div style="width:92px;height:22px;background:#e2e8f0;border-radius:20px;"></div>
            <div style="height:8px;background:#e2e8f0;border-radius:999px;"></div>
        </div>
    `).join('');
}

function orderHistoryVisual(order) {
    const raw = order.status || '';
    const status = raw.toLowerCase();
    const pct = Math.max(0, Math.min(100, Number(order.progress_percent) || 0));

    if (status === 'completed') {
        return {
            tone: 'is-complete',
            processLabel: 'Completed',
            processClass: 'completed',
            lifecycleLabel: 'Delivered',
            lifecycleClass: 'completed',
            icon: 'package-check',
            progress: 100
        };
    }
    if (status.includes('hold') || status === 'paused') {
        return {
            tone: 'is-hold',
            processLabel: raw || 'On Hold',
            processClass: 'on-hold',
            lifecycleLabel: 'On Hold',
            lifecycleClass: 'on-hold',
            icon: 'pause-circle',
            progress: pct
        };
    }
    if (status === 'cancelled') {
        return {
            tone: 'is-hold',
            processLabel: 'Cancelled',
            processClass: 'cancelled',
            lifecycleLabel: 'Cancelled',
            lifecycleClass: 'cancelled',
            icon: 'x-circle',
            progress: pct
        };
    }
    return {
        tone: 'is-active',
        processLabel: (raw === 'Pending' || raw === 'Pending Verification') ? raw : 'Processing',
        processClass: 'processing',
        lifecycleLabel: 'In Progress',
        lifecycleClass: 'in-progress',
        icon: 'package',
        progress: pct
    };
}

function renderClientOrdersPagination(pagination) {
    const box = document.getElementById('client-orders-pagination');
    if (!box) return;
    const totalPages = Number(pagination?.total_pages || 1);
    const page = Number(pagination?.page || 1);
    if (totalPages <= 1) {
        box.style.display = 'none';
        box.innerHTML = '';
        return;
    }
    box.style.display = 'flex';
    box.innerHTML = `
        <button type="button" class="btn-secondary" ${page <= 1 ? 'disabled' : ''} onclick="loadClientOrders(${page - 1})">Previous</button>
        <span style="font-size:0.8rem;font-weight:700;color:#64748b;">Page ${page} of ${totalPages}</span>
        <button type="button" class="btn-secondary" ${page >= totalPages ? 'disabled' : ''} onclick="loadClientOrders(${page + 1})">Next</button>
    `;
}

async function loadClientOrders(page) {
    if (page) clientOrdersPage = page;
    const search = '';
    const status = document.getElementById('filter-order-status')?.value || '';
    const sort = document.getElementById('filter-order-sort')?.value || 'date_desc';

    showClientOrdersError('');
    renderClientOrdersSkeleton();

    try {
        const url = `/api/client/orders?search=${encodeURIComponent(search)}&status=${encodeURIComponent(status)}&sort=${encodeURIComponent(sort)}&page=${clientOrdersPage}&limit=12`;
        const res = await fetch(url);
        const data = await res.json().catch(() => ({}));

        if (!res.ok || data.status !== 'success') {
            showClientOrdersError('Unable to load your orders. Please try again.');
            const container = document.getElementById('orders-cards-container');
            if (container) container.innerHTML = '';
            renderClientOrdersPagination(null);
            return;
        }

        if (data.stats) {
            const totalEl = document.getElementById('stat-total-orders');
            const completedEl = document.getElementById('stat-completed-orders');
            const progressEl = document.getElementById('stat-in-progress-orders');
            const spentEl = document.getElementById('stat-total-spent');
            if (totalEl) totalEl.textContent = data.stats.total_orders;
            if (completedEl) completedEl.textContent = data.stats.completed;
            if (progressEl) progressEl.textContent = data.stats.in_progress;
            if (spentEl) spentEl.textContent = data.stats.total_spent;
        }

        const filteredTotal = Number(data.pagination?.total ?? data.orders.length);
        const countEl = document.getElementById('orders-count-label');
        if (countEl) countEl.textContent = `${filteredTotal} ${filteredTotal === 1 ? 'Order' : 'Orders'}`;

        renderOrderCardsGrid(data.orders || []);
        renderClientOrdersPagination(data.pagination);
    } catch (err) {
        console.error('Error loading orders:', err);
        showClientOrdersError('Unable to load your orders. Please try again.');
        const container = document.getElementById('orders-cards-container');
        if (container) container.innerHTML = '';
        renderClientOrdersPagination(null);
    }
}

function renderOrderCardsGrid(orders) {
    const container = document.getElementById('orders-cards-container');
    if (!container) return;

    if (!orders.length) {
        container.innerHTML = `
            <div style="grid-column: 1 / -1; text-align:center; padding:48px; background:#fff; border-radius:16px; border:1px solid #e2e8f0;">
                <i data-lucide="inbox" style="width:48px; height:48px; color:#94a3b8;"></i>
                <h3 style="font-size:1.1rem; font-weight:700; margin-top:12px;">No orders found</h3>
                <p style="color:#64748b; font-size:0.85rem;">Try adjusting your filter or search terms.</p>
            </div>
        `;
        lucide.createIcons();
        return;
    }

    container.innerHTML = orders.map(order => {
        const visual = orderHistoryVisual(order);
        const price = parseFloat(order.total ?? order.price ?? 0).toFixed(2);
        return `
            <article class="history-order-card ${visual.tone}" role="button" tabindex="0" onclick="openOrderDetailsModal(${order.id})" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();openOrderDetailsModal(${order.id});}">
                <div class="history-order-top">
                    <div class="history-order-id">
                        <div class="history-order-icon"><i data-lucide="${visual.icon}"></i></div>
                        <div>
                            <div class="history-order-number">${escapeHtml(order.order_number)}</div>
                            <div class="history-order-date">${escapeHtml(formatDate(order.created_at))}</div>
                        </div>
                    </div>
                    <div class="history-order-aside">
                        <div class="history-order-price">£${price}</div>
                        <span class="status-badge ${visual.processClass}">${escapeHtml(visual.processLabel)}</span>
                    </div>
                </div>
                <div class="history-order-service">${escapeHtml(order.service_name || '')}</div>
                <span class="status-badge ${visual.lifecycleClass}">${escapeHtml(visual.lifecycleLabel)}</span>
                <div class="history-order-progress">
                    <div class="progress-track">
                        <div class="progress-bar" style="width:${visual.progress}%;"></div>
                    </div>
                    <span>${visual.progress}%</span>
                </div>
                <button type="button" class="history-order-details" onclick="event.stopPropagation(); openOrderDetailsModal(${order.id})">
                    <i data-lucide="chevron-down"></i> View details
                </button>
            </article>
        `;
    }).join('');

    lucide.createIcons();
}

// ----------------------------------------------------
// ORDER DETAILS & TIMELINE MODAL
// ----------------------------------------------------
async function openOrderDetailsModal(orderId) {
    try {
        const res = await fetch(`/api/client/orders/${orderId}`);
        const data = await res.json();
        
        if (data.status === 'success') {
            const order = data.order;
            const timeline = data.timeline || [];
            const invoice = data.invoice;
            const lines = data.line_items || [];
            
            // Header elements
            const numEl = document.getElementById('modal-order-number');
            if (numEl) numEl.textContent = `Order ${order.order_number}`;
            
            const badgeEl = document.getElementById('modal-order-status-badge');
            if (badgeEl) {
                badgeEl.textContent = order.status;
                badgeEl.className = `status-badge ${order.status.toLowerCase().replace(/\s+/g, '-')}`;
            }
            
            const dateEl = document.getElementById('modal-order-date');
            if (dateEl) dateEl.textContent = `Placed on ${formatDate(order.created_at)}`;
            
            const serviceEl = document.getElementById('modal-service-name');
            if (serviceEl) serviceEl.textContent = order.service_name;
            
            const payStatusEl = document.getElementById('modal-payment-status');
            if (payStatusEl) {
                const isPaid = order.status === 'Completed' || (invoice && invoice.status === 'Paid');
                payStatusEl.textContent = isPaid ? 'Paid' : (order.status === 'Cancelled' ? 'Cancelled' : 'Processing');
                payStatusEl.style.color = isPaid ? '#059669' : (order.status === 'Cancelled' ? '#dc2626' : '#d97706');
            }
            
            const totalEl = document.getElementById('modal-order-total');
            const formattedTotal = `£${parseFloat(order.total || order.price).toFixed(2)}`;
            if (totalEl) totalEl.textContent = formattedTotal;
            const currencyNote = document.getElementById('modal-order-currency');
            if (currencyNote) currencyNote.textContent = 'GBP';

            const setText = (id, value) => {
                const el = document.getElementById(id);
                if (el) el.textContent = value;
            };
            setText('modal-summary-number', order.order_number || '—');
            setText('modal-summary-date', formatDate(order.created_at) || '—');
            setText('modal-summary-status', order.status || '—');
            setText('modal-summary-progress', `${order.progress_percent || 0}%`);
            setText('modal-summary-payment', document.getElementById('modal-payment-status')?.textContent || '—');
            setText('modal-summary-total', formattedTotal);
            
            // Company & Client Box
            const compNameEl = document.getElementById('modal-company-name');
            if (compNameEl) compNameEl.textContent = order.company_name || 'Individual Client Order';
            
            const compMetaEl = document.getElementById('modal-company-meta');
            if (compMetaEl) {
                const regStr = order.company_number ? `Reg #${order.company_number} · ` : '';
                const addrStr = order.company_address || order.client_address || 'UK Address File';
                compMetaEl.textContent = `${regStr}${addrStr}`;
            }
            
            const clientNameEl = document.getElementById('modal-client-name');
            if (clientNameEl) clientNameEl.textContent = order.client_name || currentUser?.full_name || 'Client Account';
            
            const clientEmailEl = document.getElementById('modal-client-email');
            if (clientEmailEl) clientEmailEl.textContent = order.client_email || currentUser?.email || '—';
            const clientPhoneEl = document.getElementById('modal-client-phone');
            if (clientPhoneEl) clientPhoneEl.textContent = formatUkPhone(order.client_phone);
            
            // Line Items
            const linesBox = document.getElementById('modal-order-line-items');
            if (linesBox) {
                const itemVisual = orderHistoryVisual(order);
                if (lines.length > 0) {
                    linesBox.innerHTML = lines.map(item => {
                        const cat = item.category_name ? `<span class="status-badge pending" style="font-size:0.7rem; padding:2px 6px; margin-right:6px;">${escapeHtml(item.category_name)}</span>` : '';
                        const qty = item.quantity || 1;
                        const lineTotal = parseFloat(item.line_total || item.price || 0).toFixed(2);
                        return `
                            <div style="display:flex; justify-content:space-between; align-items:flex-start; gap:12px; padding:10px 0; border-bottom:1px solid #f1f5f9;">
                                <div>
                                    ${cat}<strong style="color:#0f172a;">${escapeHtml(item.product_name)}</strong>
                                    <div style="font-size:0.78rem; color:#64748b; margin-top:4px;">Quantity: ${qty} · ${escapeHtml(itemVisual.lifecycleLabel)} · ${itemVisual.progress}%</div>
                                </div>
                                <div style="text-align:right;">
                                    <div style="font-weight:700; color:#0f172a;">£${lineTotal}</div>
                                    <span class="status-badge ${itemVisual.processClass}" style="margin-top:6px;">${escapeHtml(itemVisual.processLabel)}</span>
                                </div>
                            </div>
                        `;
                    }).join('');
                } else {
                    linesBox.innerHTML = `
                        <div style="display:flex; justify-content:space-between; align-items:flex-start; gap:12px; padding:8px 0;">
                            <div>
                                <strong style="color:#0f172a;">${escapeHtml(order.service_name)}</strong>
                                <div style="font-size:0.78rem; color:#64748b; margin-top:4px;">Quantity: 1 · ${escapeHtml(itemVisual.lifecycleLabel)} · ${itemVisual.progress}%</div>
                            </div>
                            <div style="text-align:right;">
                                <div style="font-weight:700; color:#0f172a;">£${parseFloat(order.total || order.price).toFixed(2)}</div>
                                <span class="status-badge ${itemVisual.processClass}" style="margin-top:6px;">${escapeHtml(itemVisual.processLabel)}</span>
                            </div>
                        </div>
                    `;
                }
            }
            
            // Progress Bar & Text
            const progTextEl = document.getElementById('modal-order-progress-text');
            if (progTextEl) progTextEl.textContent = `${order.progress_percent || 0}% Complete`;
            
            const progBarEl = document.getElementById('modal-order-progress-bar');
            if (progBarEl) {
                progBarEl.style.width = `${order.progress_percent || 0}%`;
                progBarEl.classList.toggle('completed', order.progress_percent === 100);
            }
            
            // Render Timeline
            const timelineBox = document.getElementById('modal-order-timeline');
            if (timelineBox) {
                timelineBox.innerHTML = timeline.map(step => {
                    const stepClass = step.status.toLowerCase();
                    const icon = step.status === 'Completed' ? '✓' : (step.status === 'Current' ? '•' : '');
                    return `
                        <div class="timeline-step ${stepClass}">
                            <div class="timeline-dot">${icon}</div>
                            <div class="timeline-content">
                                <h5>${escapeHtml(step.title)}</h5>
                                <p>${escapeHtml(step.status)} • ${step.step_date ? formatDate(step.step_date) : 'Pending'}</p>
                            </div>
                        </div>
                    `;
                }).join('');
            }
            
            const docsBox = document.getElementById('modal-order-documents');
            if (docsBox) {
                const docs = data.documents || [];
                docsBox.innerHTML = docs.length ? docs.map((doc) => `
                    <div style="display:flex; justify-content:space-between; gap:8px; align-items:center; padding:8px 0; border-bottom:1px solid #f1f5f9;">
                        <div>
                            <strong>${escapeHtml(doc.name)}</strong>
                            <div style="font-size:0.75rem; color:#64748b;">${escapeHtml(doc.category || doc.file_type || 'Document')} · ${escapeHtml(formatDate(doc.created_at))} · ${escapeHtml(doc.status || '')}</div>
                        </div>
                        <div class="doc-file-actions">
                            ${documentActionButtons(doc)}
                        </div>
                    </div>
                `).join('') : '<p style="color:#64748b; margin:0;">No documents are available for this order yet.</p>';
            }

            const supportBox = document.getElementById('modal-order-support');
            if (supportBox) {
                const tickets = data.tickets || [];
                if (tickets.length) {
                    supportBox.innerHTML = tickets.map((t) => `
                        <div style="display:flex; justify-content:space-between; gap:8px; align-items:center; padding:8px 0; border-bottom:1px solid #f1f5f9;">
                            <div>
                                <strong>${escapeHtml(t.ticket_number)}</strong>
                                <div style="font-size:0.75rem; color:#64748b;">${escapeHtml(t.subject || '')} · ${escapeHtml(t.status || '')}</div>
                            </div>
                            <button type="button" class="btn-secondary" style="padding:4px 10px; font-size:0.75rem;" onclick="event.stopPropagation(); closeOrderModal(); switchView('client-support');">Open Support</button>
                        </div>
                    `).join('');
                } else {
                    supportBox.innerHTML = `
                        <div style="display:flex; justify-content:space-between; gap:8px; align-items:center;">
                            <p style="color:#64748b; margin:0;">No support tickets on this order.</p>
                            <button type="button" class="btn-secondary" style="padding:4px 10px; font-size:0.75rem;" onclick="event.stopPropagation(); closeOrderModal(); switchView('client-support');">Order Support</button>
                        </div>
                    `;
                }
            }

            const modal = document.getElementById('order-details-modal');
            if (modal) modal.classList.add('active');
            lucide.createIcons();
        } else {
            alert(data.message || 'Unable to open this order.');
        }
    } catch (err) {
        console.error('Error fetching order details:', err);
        alert('Unable to load order details. Please try again.');
    }
}

function closeOrderModal() {
    const modal = document.getElementById('order-details-modal');
    if (modal) modal.classList.remove('active');
}

function onOrderModalBackdrop(event) {
    if (event.target && event.target.id === 'order-details-modal') {
        closeOrderModal();
    }
}

document.addEventListener('keydown', (event) => {
    const previewModal = document.getElementById('modal-document-preview');
    const previewOpen = previewModal && previewModal.classList.contains('active');
    if (previewOpen) {
        const typing = event.target && (event.target.tagName === 'INPUT' || event.target.tagName === 'TEXTAREA' || event.target.isContentEditable);
        if (!typing) {
            if (event.key === '+' || event.key === '=' || event.code === 'NumpadAdd') {
                const previewBody = document.getElementById('document-preview-body');
                if (!previewBody || !previewBody.classList.contains('is-image')) return;
                event.preventDefault();
                zoomDocumentPreview(1);
                return;
            }
            if (event.key === '-' || event.key === '_' || event.code === 'NumpadSubtract') {
                const previewBody = document.getElementById('document-preview-body');
                if (!previewBody || !previewBody.classList.contains('is-image')) return;
                event.preventDefault();
                zoomDocumentPreview(-1);
                return;
            }
            if (event.key === '0' || event.code === 'Numpad0') {
                const previewBody = document.getElementById('document-preview-body');
                if (!previewBody || !previewBody.classList.contains('is-image')) return;
                event.preventDefault();
                const origin = documentPreviewZoomOrigin(previewBody, previewBody.getBoundingClientRect().left + previewBody.clientWidth / 2, previewBody.getBoundingClientRect().top + previewBody.clientHeight / 2);
                setDocumentPreviewZoom(1, origin);
                return;
            }
            if (event.key === 'r' || event.key === 'R') {
                const previewBody = document.getElementById('document-preview-body');
                if (!previewBody || !previewBody.classList.contains('is-image')) return;
                event.preventDefault();
                rotateDocumentPreview();
                return;
            }
        }
        if (event.key === 'Escape') {
            closeDocumentPreview();
            return;
        }
    }
    if (event.key !== 'Escape') return;
    const deleteModal = document.getElementById('modal-delete-order');
    if (deleteModal && deleteModal.classList.contains('active')) {
        closeDeleteOrderModal();
        return;
    }
    const orderModal = document.getElementById('order-details-modal');
    if (orderModal && orderModal.classList.contains('active')) {
        closeOrderModal();
    }
});

// ----------------------------------------------------
// CLIENT DASHBOARD (Brixen Consultants Layout)
// ----------------------------------------------------
let clientDashLoadSeq = 0;
let clientDashDocsById = {};

function timeOfDayGreeting(date) {
    const hour = (date || new Date()).getHours();
    if (hour < 12) return 'Good morning';
    if (hour < 17) return 'Good afternoon';
    return 'Good evening';
}

function resetClientDashboardView() {
    clientDashLoadSeq += 1;
    clientDashDocsById = {};
    const hello = document.getElementById('portal-home-hello');
    const dateEl = document.getElementById('portal-home-date');
    if (hello) hello.textContent = '';
    if (dateEl) dateEl.textContent = '';
    ['dash-card-businesses', 'dash-card-orders', 'dash-card-documents', 'dash-card-pending'].forEach((id) => {
        const el = document.getElementById(id);
        if (el) el.textContent = '—';
    });
    const ready = document.getElementById('portal-home');
    const skel = document.getElementById('portal-home-skel');
    const errorBox = document.getElementById('dash-error');
    if (ready) ready.hidden = true;
    if (skel) {
        skel.hidden = false;
        skel.setAttribute('aria-busy', 'true');
    }
    if (errorBox) {
        errorBox.style.display = 'none';
        errorBox.textContent = '';
    }
}

function showClientDashboardSkeleton() {
    const ready = document.getElementById('portal-home');
    const skel = document.getElementById('portal-home-skel');
    if (ready) ready.hidden = true;
    if (skel) {
        skel.hidden = false;
        skel.setAttribute('aria-busy', 'true');
    }
}

function showClientDashboardReady() {
    const ready = document.getElementById('portal-home');
    const skel = document.getElementById('portal-home-skel');
    if (skel) {
        skel.hidden = true;
        skel.setAttribute('aria-busy', 'false');
    }
    if (ready) ready.hidden = false;
}

function dashStatusClass(status) {
    const raw = String(status || '').toLowerCase();
    if (/completed|paid|active|approved|verified|good standing/.test(raw)) return 'completed';
    if (/processing|in progress|in-progress|current/.test(raw)) return 'processing';
    if (/cancel|reject|overdue|fail|expired/.test(raw)) return 'cancelled';
    return 'pending';
}

async function loadClientDashboard() {
    const loadId = ++clientDashLoadSeq;
    const errorBox = document.getElementById('dash-error');
    if (errorBox) {
        errorBox.style.display = 'none';
        errorBox.textContent = '';
    }
    showClientDashboardSkeleton();
    const hello = document.getElementById('portal-home-hello');
    const dateEl = document.getElementById('portal-home-date');
    if (hello) hello.textContent = '';
    if (dateEl) dateEl.textContent = '';

    try {
        const res = await fetch('/api/client/dashboard', { credentials: 'same-origin' });
        if (loadId !== clientDashLoadSeq || !currentUser) return;
        if (!res.ok) throw new Error('Unable to load dashboard data. Please try again.');
        const data = await res.json();
        if (loadId !== clientDashLoadSeq || !currentUser) return;

        if (data.status === 'success') {
            const displayName = String(data.user_name || '').trim();
            const greeting = data.greeting || timeOfDayGreeting();
            const todayLabel = new Date().toLocaleDateString('en-GB', {
                weekday: 'long', day: 'numeric', month: 'long', year: 'numeric'
            });
            if (dateEl) dateEl.textContent = todayLabel;
            if (hello) hello.textContent = displayName ? `${greeting}, ${displayName}` : greeting;

            const setCount = (id, value) => {
                const el = document.getElementById(id);
                if (el) el.textContent = String(value ?? 0);
            };
            setCount('dash-card-businesses', data.total_companies);
            setCount('dash-card-orders', data.active_orders_count);
            setCount('dash-card-documents', data.documents_count);
            setCount('dash-card-pending', data.pending_actions_count);

            renderClientDashAttention(data.pending_actions || []);
            renderActiveOrdersGrid(data.active_orders || []);
            renderDashboardCompanies(data.companies || []);
            renderClientDashDocuments(data.recent_documents || []);
            showClientDashboardReady();
            safeCreateIcons();
        } else {
            throw new Error('Unable to load dashboard data. Please try again.');
        }
    } catch (err) {
        if (loadId !== clientDashLoadSeq || !currentUser) return;
        console.error('Error loading dashboard:', err);
        const skel = document.getElementById('portal-home-skel');
        const ready = document.getElementById('portal-home');
        if (skel) {
            skel.hidden = true;
            skel.setAttribute('aria-busy', 'false');
        }
        if (ready) ready.hidden = true;
        if (errorBox) {
            errorBox.style.display = 'block';
            errorBox.innerHTML = 'Unable to load dashboard data. <button type="button" class="portal-home-link" onclick="loadClientDashboard()">Retry</button>';
        }
        safeCreateIcons();
    }
}

function runDashAttentionAction(btn) {
    if (!btn) return;
    const orderId = Number(btn.getAttribute('data-order-id') || 0);
    const docId = Number(btn.getAttribute('data-document-id') || 0);
    const view = btn.getAttribute('data-view') || '';
    if (orderId) {
        openOrderDetailsModal(orderId);
        return;
    }
    if (docId) {
        const doc = clientDashDocsById[docId];
        if (doc) {
            openDocumentPreview(doc.id, doc.name, doc.file_type);
            return;
        }
        switchView('client-documents');
        return;
    }
    if (view) switchView(view);
}

function renderClientDashAttention(actions) {
    const box = document.getElementById('portal-home-attention');
    if (!box) return;
    const list = Array.isArray(actions) ? actions : [];
    box.hidden = false;
    if (!list.length) {
        box.innerHTML = `<p class="portal-home-caught-up">You're all caught up.</p>`;
        return;
    }
    box.innerHTML = `
        <h2 class="portal-home-attention-title">Needs Your Attention</h2>
        ${list.map((item) => {
            const icon = escapeHtml(item.icon || 'alert-circle');
            const view = escapeHtml(item.view || '');
            const orderId = Number(item.order_id || 0) || '';
            const docId = Number(item.document_id || 0) || '';
            return `
                <div class="portal-home-action">
                    <div class="portal-home-action-icon" aria-hidden="true"><i data-lucide="${icon}"></i></div>
                    <div>
                        <h3>${escapeHtml(item.title || '')}</h3>
                        <p>${escapeHtml(item.detail || '')}</p>
                    </div>
                    <button type="button" class="btn-primary" data-view="${view}" data-order-id="${orderId}" data-document-id="${docId}" onclick="runDashAttentionAction(this)">${escapeHtml(item.action_label || 'View')}</button>
                </div>
            `;
        }).join('')}
    `;
}

function renderActiveOrdersGrid(orders) {
    const grid = document.getElementById('dash-active-orders-grid');
    const section = document.getElementById('portal-home-orders');
    if (!grid) return;
    const list = (Array.isArray(orders) ? orders : []).slice(0, 4);
    if (!list.length) {
        if (section) section.hidden = true;
        grid.innerHTML = '';
        return;
    }
    if (section) section.hidden = false;
    grid.innerHTML = list.map((order) => {
        const progress = Math.max(0, Math.min(100, Number(order.progress_percent || 0)));
        const statusClass = dashStatusClass(order.status);
        return `
            <button type="button" class="portal-home-row" onclick="openOrderDetailsModal(${Number(order.id)})">
                <div>
                    <div class="portal-home-row-title">${escapeHtml(order.order_number || 'Order')}</div>
                    <div class="portal-home-row-meta">${escapeHtml(order.service_name || '')}</div>
                </div>
                <div class="portal-home-row-meta">${escapeHtml(formatDate(order.created_at) || '')}</div>
                <span class="status-badge ${statusClass}">${escapeHtml(order.status || '')}</span>
                <div class="portal-home-row-progress">
                    <div class="portal-home-progress-track" aria-hidden="true"><div class="portal-home-progress-bar" style="width:${progress}%;"></div></div>
                    <span class="portal-home-progress-label">${progress}%</span>
                </div>
            </button>
        `;
    }).join('');
}

function companyCountryLabel(company) {
    const number = String((company && company.company_number) || '').trim().toUpperCase();
    if (!number || number.startsWith('REG-') || looksLikeUkCompanyNumber(number)) {
        return 'United Kingdom';
    }
    const raw = String((company && company.country) || '').trim();
    const office = String((company && company.reg_office) || '');
    const last = office.split(',').pop().trim();
    const ukNames = /^(uk|gb|gbr|united kingdom|great britain|britain|england|scotland|wales|northern ireland|n\.? ireland|eng|pk|pakistan)$/i;
    const ukPostcode = /^[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}$/i;
    if (ukNames.test(raw) || ukPostcode.test(raw) || ukNames.test(last) || ukPostcode.test(last)) {
        return 'United Kingdom';
    }
    if (/united kingdom|\bgreat britain\b|\benland\b|\bscotland\b|\bwales\b|\bnorthern ireland\b|\buk\b|\bgb\b|\blondon\b/i.test(office)) {
        return 'United Kingdom';
    }
    return 'United Kingdom';
}

function looksLikeUkCompanyNumber(companyNumber) {
    const num = String(companyNumber || '').trim().toUpperCase().replace(/\s+/g, '');
    return /^(\d{6,8}|[A-Z]{2}\d{5,6})$/.test(num);
}

function normalizeCompanyNameKey(name) {
    return String(name || '').toUpperCase().replace(/[^A-Z0-9]/g, '').replace(/(LTD|LIMITED|PLC|LLP|CIC)$/, '');
}

function companiesHouseMatchForName(name, matches) {
    const needle = normalizeCompanyNameKey(name);
    if (!needle || needle.length < 4) return null;
    const hits = (matches || []).filter((match) => normalizeCompanyNameKey(match && match.name) === needle);
    if (hits.length === 1) return hits[0];
    const active = hits.filter((match) => String((match && match.status) || '').toLowerCase() === 'active');
    if (active.length === 1) return active[0];
    if ((matches || []).length && normalizeCompanyNameKey(matches[0].name) === needle) return matches[0];
    return null;
}

function portfolioDetailCompanySnapshot() {
    const company = { ...((portfolioDetailCache && portfolioDetailCache.company) || {}) };
    const numberEl = document.getElementById('portfolio-detail-number-field');
    const numberText = String((numberEl && numberEl.textContent) || '').trim();
    if (numberText && numberText !== '—') {
        company.company_number = numberText;
    }
    if (looksLikeUkCompanyNumber(company.company_number)) {
        company.is_registered = true;
    }
    return company;
}

function portfolioCompanyCanRename(company) {
    if (!isAdminShellUser(currentUser)) return false;
    if (company && company.is_registered === true) return false;
    if (isRegisteredCompany(company)) return false;
    if (looksLikeUkCompanyNumber(company && company.company_number)) return false;
    return true;
}

function portfolioCompanyNeedsChMatch(company) {
    return isRegisteredCompany(company);
}

function portfolioDirectorNames(company) {
    if (Array.isArray(company && company.directors) && company.directors.length) {
        return company.directors.map((name) => String(name || '').trim()).filter(Boolean);
    }
    const raw = String((company && (company.owner_name || company.director)) || '').trim();
    if (!raw || raw === '—') return [];
    return raw.split(/\s*·\s*|\s*;\s*|\n+/).map((part) => part.trim()).filter(Boolean);
}

function isUkCompany(company) {
    return /united kingdom|\buk\b/i.test(companyCountryLabel(company));
}

function companyStatusLabel(company) {
    return company && (company.status || company.account_status || 'Active');
}

function isCompanyActive(company) {
    const status = String(companyStatusLabel(company) || '');
    return /active|good standing/i.test(status);
}

function formatUkNumericDate(dateStr) {
    const raw = String(dateStr || '').trim();
    const iso = raw.match(/^(\d{4})-(\d{2})-(\d{2})/);
    if (iso) return `${iso[3]}/${iso[2]}/${iso[1]}`;
    return raw || '—';
}

function portfolioDeadlineText(company) {
    const attention = portfolioAttentionIssues(company);
    if (attention.length) {
        return attention.map((item) => item.title || item.code).filter(Boolean).join(' · ');
    }
    const items = Array.isArray(company && company.deadlines) ? company.deadlines : [];
    if (!items.length) return 'No upcoming deadlines';
    return items.map((item) => `${item.label}: ${formatUkNumericDate(item.date)}`).join(' · ');
}

function portfolioAttentionIssues(company) {
    const items = Array.isArray(company && company.attention)
        ? company.attention.filter((item) => item && item.code)
        : [];
    if (items.length) return items;
    const addr = String((company && (company.registered_address || company.reg_office)) || '');
    if (/companies house default address/i.test(addr) || (/cf14\s*8lh/i.test(addr) && /default/i.test(addr))) {
        return [{ code: 'default_address', title: 'Default Companies House address' }];
    }
    return [];
}

function companyNeedsAttention(company) {
    return Boolean(company && company.needs_attention) || portfolioAttentionIssues(company).length > 0;
}

function isRegisteredCompany(company) {
    if (company && typeof company.is_registered === 'boolean') return company.is_registered;
    const num = String((company && company.company_number) || '').trim().toUpperCase();
    return Boolean(num) && !num.startsWith('REG-');
}

function portfolioRegisteredAddressText(company) {
    if (!isRegisteredCompany(company)) return '——————';
    const addr = String((company && (company.registered_address || company.reg_office)) || '').trim();
    if (!addr || /^(united kingdom|uk|none|n\/a)$/i.test(addr)) return '——————';
    return addr;
}

function isCustomerUploadedDocument(doc) {
    if (!doc) return false;
    if (Number(doc.is_posted) === 1 || doc.lifecycle_status === 'POSTED_DOCUMENTS') return false;
    return Boolean(
        doc.is_customer_upload
        || doc.uploaded_by === 'Customer Upload'
        || doc.category === 'Checkout Upload'
        || doc.lifecycle_status === 'CUSTOMER_UPLOADS'
        || doc.lifecycle_status === 'REVIEW_REQUIRED'
        || doc.lifecycle_status === 'READY_FOR_APPROVAL'
        || doc.lifecycle_status === 'PROCESSING'
        || doc.lifecycle_status === 'QUARANTINE'
    );
}

function countryBadgeHtml(company) {
    return `<span class="portfolio-badge-uk"><span aria-hidden="true">🇬🇧</span> UK</span>`;
}

let clientCompaniesCache = [];
let clientPendingCache = [];
let adminCompaniesCache = [];
let adminPendingCache = [];
let adminCompaniesSelection = new Set();
let adminCompaniesSearchTimer = null;
let portfolioDetailCache = null;
let activePortfolioDocumentsTab = 'customer';
let adminCompanyClientsCache = [];
const companyRegFilter = { client: 'all', admin: 'all' };

function portfolioSkeletonHtml() {
    return Array.from({ length: 3 }).map(() => `
        <div class="portfolio-card portfolio-skeleton" aria-hidden="true">
            <div class="portfolio-card-head">
                <div class="portfolio-skeleton-icon"></div>
                <div class="portfolio-card-titles" style="display:flex; flex-direction:column; gap:8px;">
                    <div class="portfolio-skeleton-line" style="width:70%;"></div>
                    <div class="portfolio-skeleton-line" style="width:40%;"></div>
                </div>
            </div>
            <div class="portfolio-card-body">
                <div class="portfolio-skeleton-line" style="width:55%;"></div>
            </div>
        </div>
    `).join('');
}

function renderPendingRegistrationCards(pending, isAdmin) {
    const list = Array.isArray(pending) ? pending : [];
    return list.map((item) => {
        const orderId = Number(item.order_id);
        const title = isAdmin ? (item.client_name || 'Website client') : 'Company registration in progress';
        const adminForm = isAdmin ? `
            <form class="portfolio-pending-form" onsubmit="event.preventDefault(); confirmPendingCompanyName(${orderId}, this);">
                <div class="ch-search-wrap">
                    <input type="text" name="company_name" required minlength="2" placeholder="Search Companies House" autocomplete="off" oninput="searchCompaniesHouse(this)">
                    <input type="hidden" name="company_number">
                    <input type="hidden" name="inc_date">
                    <input type="hidden" name="company_status">
                    <input type="hidden" name="reg_office">
                    <div class="ch-search-results" hidden></div>
                </div>
                <button type="submit" class="btn-primary">Create card</button>
            </form>
        ` : '';
        return `
            <article class="portfolio-card is-pending-registration" data-pending-order-id="${orderId}">
                <div class="portfolio-card-head">
                    <div class="portfolio-card-icon" aria-hidden="true"><i data-lucide="building-2"></i></div>
                    <div class="portfolio-card-titles">
                        <h3>${escapeHtml(title)}</h3>
                        <p>${escapeHtml(item.order_number || 'Website order')} · ${escapeHtml(item.service_name || 'Company registration')}</p>
                    </div>
                    <div class="portfolio-card-badges">
                        <span class="portfolio-badge-uk">🇬🇧 UK</span>
                        <span class="portfolio-badge-status is-pending">awaiting name</span>
                    </div>
                </div>
                <div class="portfolio-card-body">${isAdmin
                    ? 'Type the company name or number to search Companies House, then create the card from the official record.'
                    : 'Your website company registration is in progress. The company card appears here once the registered name is confirmed.'}</div>
                ${adminForm}
                <div class="portfolio-card-foot">
                    <span>${escapeHtml(item.status || 'Processing')}</span>
                    ${isAdmin && canDeleteCompanies()
                        ? `<button type="button" class="portfolio-delete-btn" title="Remove" aria-label="Remove awaiting-name card" onclick="event.stopPropagation(); dismissPendingCompany(${orderId})"><i data-lucide="trash-2"></i></button>`
                        : '<i data-lucide="arrow-right"></i>'}
                </div>
            </article>
        `;
    }).join('');
}

function portfolioGridHeading(title, copy) {
    return `<div class="portfolio-grid-heading"><h2>${escapeHtml(title)}</h2>${copy ? `<p>${escapeHtml(copy)}</p>` : ''}</div>`;
}

function renderRegisteredCompanyCard(company, isAdmin) {
    const active = isCompanyActive(company);
    const attention = portfolioAttentionIssues(company);
    const needsAttention = attention.length > 0;
    const companyId = Number(company.id);
    const selected = isAdmin && adminCompaniesSelection.has(companyId);
    const selectHtml = isAdmin
        ? `<label class="portfolio-card-select" onclick="event.stopPropagation()">
                <input type="checkbox" class="adm-company-select" data-company-id="${companyId}" ${selected ? 'checked' : ''} onchange="toggleAdminCompanySelection(${companyId}, this.checked)">
           </label>`
        : '';
    return `
        <article class="portfolio-card${needsAttention ? ' is-attention' : ''}${selected ? ' is-selected' : ''}" data-company-id="${companyId}" role="button" tabindex="0" onclick="openCompanyPortfolioDetail(${companyId})" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();openCompanyPortfolioDetail(${companyId});}">
            <div class="portfolio-card-head">
                ${selectHtml}
                <div class="portfolio-card-icon" aria-hidden="true"><i data-lucide="building-2"></i></div>
                <div class="portfolio-card-titles">
                    <h3>${escapeHtml(company.name || 'Company')}</h3>
                    <p style="display:flex; align-items:center; gap:6px; flex-wrap:wrap; margin-top:2px;">
                        <span>#${escapeHtml(company.company_number || '—')}</span>
                        ${company.b2b_id ? `<span class="b2b-badge">${escapeHtml(company.b2b_id)}</span>` : ''}
                    </p>
                    ${(() => {
                        const names = portfolioDirectorNames(company);
                        if (!isRegisteredCompany(company) || !names.length) return '';
                        return `<p class="portfolio-card-owner">${names.length > 1 ? 'Directors' : 'Director'} · ${escapeHtml(names.join(' · '))}</p>`;
                    })()}
                    <p class="portfolio-card-address">${escapeHtml(portfolioRegisteredAddressText(company))}</p>
                    ${isAdmin && !isRegisteredCompany(company) ? '<p class="portfolio-card-rename-hint">Open to change the name before application.</p>' : ''}
                </div>
                <div class="portfolio-card-badges">
                    ${countryBadgeHtml(company)}
                    ${needsAttention ? '<span class="portfolio-badge-status is-attention">attention</span>' : ''}
                    <span class="portfolio-badge-status${active ? '' : ' is-pending'}">${escapeHtml(companyStatusLabel(company))}</span>
                </div>
            </div>
            <div class="portfolio-card-body">${escapeHtml(portfolioDeadlineText(company))}</div>
            <div class="portfolio-card-foot">
                <span onclick="event.stopPropagation(); openCompanyPortfolioDetail(${companyId})">View details</span>
                ${isAdmin && canDeleteCompanies()
                    ? `<button type="button" class="portfolio-delete-btn" title="Delete" aria-label="Delete company" onclick="event.stopPropagation(); deleteCompanyFromPortfolio(${companyId})"><i data-lucide="trash-2"></i></button>`
                    : '<i data-lucide="arrow-right"></i>'}
            </div>
        </article>
    `;
}

function updateCompanyRegFilterButtons(scope, filter, chCount, pendingCount, attentionCount) {
    document.querySelectorAll(`[data-company-reg-scope="${scope}"]`).forEach((btn) => {
        const value = btn.getAttribute('data-company-reg-filter');
        btn.classList.toggle('is-active', value === filter);
        btn.classList.toggle('has-items', value === 'attention' && attentionCount > 0);
        const countEl = btn.querySelector('[data-count]');
        if (!countEl) return;
        if (value === 'all') countEl.textContent = String(chCount + pendingCount);
        if (value === 'ch') countEl.textContent = String(chCount);
        if (value === 'pending') countEl.textContent = String(pendingCount);
        if (value === 'attention') countEl.textContent = String(attentionCount);
    });
}

function updatePortfolioAttentionBanner(scope, attentionCompanies) {
    const bannerId = scope === 'admin' ? 'admin-attention-banner' : 'client-attention-banner';
    const banner = document.getElementById(bannerId);
    if (!banner) return;
    const list = Array.isArray(attentionCompanies) ? attentionCompanies : [];
    if (!list.length) {
        banner.hidden = true;
        banner.innerHTML = '';
        return;
    }
    const names = list.slice(0, 3).map((company) => company.name || company.company_number || 'Company');
    const more = list.length > 3 ? ` and ${list.length - 3} more` : '';
    banner.hidden = false;
    banner.innerHTML = `<strong>${list.length} ${list.length === 1 ? 'company needs' : 'companies need'} attention</strong> at Companies House — ${escapeHtml(names.join(', '))}${escapeHtml(more)}.`;
}

function setCompanyRegFilter(scope, value) {
    companyRegFilter[scope] = value || 'all';
    if (scope === 'admin') {
        clearAdminCompaniesSelection();
        renderPortfolioCompanies(adminCompaniesCache, 'admin-portfolio-companies-grid', 'admin-portfolio-company-count', adminPendingCache);
    } else {
        renderPortfolioCompanies(clientCompaniesCache, 'portfolio-companies-grid', 'portfolio-company-count', clientPendingCache);
    }
}

function companyMatchesAdminSearch(company, query) {
    const q = String(query || '').trim().toLowerCase();
    if (!q) return true;
    const hay = [
        company && company.name,
        company && company.company_number,
        company && company.b2b_id,
        company && company.client_type,
        company && company.director,
        company && company.reg_office,
        company && company.package,
        company && company.status,
        ...(portfolioDirectorNames(company) || []),
    ].join(' ').toLowerCase();
    return hay.includes(q);
}

function companyFormationSortKey(company) {
    const inc = String((company && company.inc_date) || '').trim().slice(0, 10);
    if (/^\d{4}-\d{2}-\d{2}$/.test(inc)) return inc;
    const created = String((company && company.created_at) || '').trim().slice(0, 10);
    if (/^\d{4}-\d{2}-\d{2}$/.test(created)) return created;
    return '0000-01-01';
}

function sortCompaniesNewestFormedFirst(companies) {
    return (Array.isArray(companies) ? companies.slice() : []).sort((a, b) => {
        const kb = companyFormationSortKey(b);
        const ka = companyFormationSortKey(a);
        if (kb !== ka) return kb.localeCompare(ka);
        return Number((b && b.id) || 0) - Number((a && a.id) || 0);
    });
}

function renderPortfolioCompanies(companies, targetGridId = 'portfolio-companies-grid', targetCountId = 'portfolio-company-count', pending = []) {
    const grid = document.getElementById(targetGridId);
    const countPill = document.getElementById(targetCountId);
    let list = sortCompaniesNewestFormedFirst(companies);
    const waiting = Array.isArray(pending) ? pending : [];
    const isAdmin = targetGridId === 'admin-portfolio-companies-grid';
    const scope = isAdmin ? 'admin' : 'client';
    const filter = companyRegFilter[scope] || 'all';
    if (isAdmin) {
        const q = (document.getElementById('admin-companies-search') || {}).value || '';
        list = list.filter((company) => companyMatchesAdminSearch(company, q));
    }
    let waitingList = waiting;
    if (isAdmin) {
        const q = (document.getElementById('admin-companies-search') || {}).value || '';
        if (String(q || '').trim()) {
            waitingList = waiting.filter((item) => {
                const hay = [
                    item && item.company_name,
                    item && item.order_number,
                    item && item.client_name,
                    item && item.owner_name,
                ].join(' ').toLowerCase();
                return hay.includes(String(q).trim().toLowerCase());
            });
        }
    }
    const onCompaniesHouse = list.filter((company) => isRegisteredCompany(company));
    const notRegistered = list.filter((company) => !isRegisteredCompany(company));
    const attentionCompanies = onCompaniesHouse.filter((company) => companyNeedsAttention(company));
    const attentionIds = new Set(attentionCompanies.map((company) => Number(company.id)));
    const pendingCount = waitingList.length + notRegistered.length;
    const chCount = onCompaniesHouse.length;
    const attentionCount = attentionCompanies.length;
    if (countPill) countPill.textContent = `${list.length} UK`;
    updateCompanyRegFilterButtons(scope, filter, chCount, pendingCount, attentionCount);
    updatePortfolioAttentionBanner(scope, attentionCompanies);
    if (!grid) return;
    if (!list.length && !waitingList.length) {
        const q = isAdmin ? String((document.getElementById('admin-companies-search') || {}).value || '').trim() : '';
        grid.innerHTML = `
            <div class="portfolio-empty" role="status">
                <i data-lucide="building-2" style="width:40px; height:40px; color:var(--brand-secondary);"></i>
                <h3>${q ? 'No companies match your search.' : 'No companies in your Business Portfolio yet.'}</h3>
                <p>${q ? 'Clear the search box or try another name / company number.' : 'A card appears here after a Digital, Professional, or All Inclusive company registration is ordered on brixenconsultants.com and the company name is sent from WordPress.'}</p>
            </div>
        `;
        if (window.lucide) lucide.createIcons();
        updateAdminCompaniesSelectionCount();
        return;
    }
    const showAttention = filter === 'all' || filter === 'attention';
    const showPending = filter === 'all' || filter === 'pending';
    const showCh = filter === 'all' || filter === 'ch';
    const chWithoutAttention = filter === 'all'
        ? onCompaniesHouse.filter((company) => !attentionIds.has(Number(company.id)))
        : onCompaniesHouse;
    const attentionHtml = showAttention
        ? attentionCompanies.map((company) => renderRegisteredCompanyCard(company, isAdmin)).join('')
        : '';
    const pendingHtml = showPending ? `${renderPendingRegistrationCards(waitingList, isAdmin)}${notRegistered.map((company) => renderRegisteredCompanyCard(company, isAdmin)).join('')}` : '';
    const chHtml = showCh ? chWithoutAttention.map((company) => renderRegisteredCompanyCard(company, isAdmin)).join('') : '';
    const parts = [];
    if (showAttention && (filter === 'attention' || attentionCount > 0)) {
        parts.push(portfolioGridHeading('Attention', 'Accounts overdue, confirmation statement pending, default Companies House address, or strike-off.'));
        parts.push(attentionHtml || `<div class="portfolio-empty portfolio-empty-inline" role="status"><p>No Companies House attention items right now.</p></div>`);
    }
    if (showPending) {
        parts.push(portfolioGridHeading('Not registered yet', 'Waiting on a Companies House number. Live records are checked when you open this page.'));
        parts.push(pendingHtml || `<div class="portfolio-empty portfolio-empty-inline" role="status"><p>Every named company already has a Companies House number.</p></div>`);
    }
    if (showCh) {
        parts.push(portfolioGridHeading('Companies House', 'Official UK company numbers.'));
        parts.push(chHtml || `<div class="portfolio-empty portfolio-empty-inline" role="status"><p>No Companies House companies in this list yet.</p></div>`);
    }
    grid.innerHTML = parts.join('');
    if (window.lucide) lucide.createIcons();
    if (isAdmin) {
        const pageCb = document.getElementById('admin-companies-select-page');
        if (pageCb) {
            const visibleIds = Array.from(document.querySelectorAll('#admin-portfolio-companies-grid .adm-company-select')).map((cb) => Number(cb.getAttribute('data-company-id')));
            pageCb.checked = visibleIds.length > 0 && visibleIds.every((id) => adminCompaniesSelection.has(id));
        }
        updateAdminCompaniesSelectionCount();
    }
}

function updateAdminCompaniesSelectionCount() {
    const el = document.getElementById('admin-companies-selection-count');
    if (el) el.textContent = adminCompaniesSelection.size ? `${adminCompaniesSelection.size} selected` : '';
}

function toggleAdminCompanySelection(companyId, checked) {
    const id = Number(companyId);
    if (!id) return;
    if (checked) adminCompaniesSelection.add(id);
    else adminCompaniesSelection.delete(id);
    const card = document.querySelector(`#admin-portfolio-companies-grid article.portfolio-card[data-company-id="${id}"]`);
    if (card) card.classList.toggle('is-selected', checked);
    updateAdminCompaniesSelectionCount();
}

function toggleAdminCompaniesPageSelection(checked) {
    document.querySelectorAll('#admin-portfolio-companies-grid .adm-company-select').forEach((cb) => {
        const id = Number(cb.getAttribute('data-company-id'));
        if (!id) return;
        cb.checked = checked;
        if (checked) adminCompaniesSelection.add(id);
        else adminCompaniesSelection.delete(id);
        const card = cb.closest('article.portfolio-card');
        if (card) card.classList.toggle('is-selected', checked);
    });
    updateAdminCompaniesSelectionCount();
}

function clearAdminCompaniesSelection() {
    adminCompaniesSelection = new Set();
    const pageCb = document.getElementById('admin-companies-select-page');
    if (pageCb) pageCb.checked = false;
    document.querySelectorAll('#admin-portfolio-companies-grid .adm-company-select').forEach((cb) => {
        cb.checked = false;
        const card = cb.closest('article.portfolio-card');
        if (card) card.classList.remove('is-selected');
    });
    updateAdminCompaniesSelectionCount();
}

function onAdminCompaniesSearchInput() {
    window.clearTimeout(adminCompaniesSearchTimer);
    adminCompaniesSearchTimer = window.setTimeout(() => {
        renderPortfolioCompanies(adminCompaniesCache, 'admin-portfolio-companies-grid', 'admin-portfolio-company-count', adminPendingCache);
    }, 150);
}

function selectedAdminCompanyIds() {
    return Array.from(adminCompaniesSelection);
}

async function bulkDeleteAdminCompanies() {
    if (!canDeleteCompanies()) {
        alert('You do not have permission to delete companies.');
        return;
    }
    const ids = selectedAdminCompanyIds();
    if (!ids.length) {
        alert('Select at least one company.');
        return;
    }
    if (!confirm(`Delete ${ids.length} selected compan${ids.length === 1 ? 'y' : 'ies'}? Linked orders keep their data, but these company cards will be removed.`)) {
        return;
    }
    try {
        const res = await fetch('/api/admin/companies/bulk-delete', {
            method: 'POST',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ company_ids: ids }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data.status !== 'success') {
            throw new Error(data.message || 'Bulk delete failed.');
        }
        clearAdminCompaniesSelection();
        if (typeof loadAdminCompanies === 'function') await loadAdminCompanies();
        alert(data.message || `Deleted ${ids.length} companies.`);
    } catch (err) {
        alert(err.message || 'Bulk delete failed.');
    }
}

function fillBulkEditCompanyClientOptions() {
    const select = document.getElementById('bulk-edit-company-client');
    if (!select) return;
    const options = ['<option value="">— keep current —</option>'].concat(
        (adminCompanyClientsCache || []).map((client) =>
            `<option value="${Number(client.id)}">${escapeHtml(client.full_name || client.email || 'Client')}</option>`
        )
    );
    select.innerHTML = options.join('');
}

function openBulkEditCompaniesModal() {
    const ids = selectedAdminCompanyIds();
    if (!ids.length) {
        alert('Select at least one company.');
        return;
    }
    fillBulkEditCompanyClientOptions();
    const count = document.getElementById('bulk-edit-companies-count');
    if (count) count.textContent = `${ids.length} compan${ids.length === 1 ? 'y' : 'ies'} selected.`;
    const err = document.getElementById('bulk-edit-companies-error');
    if (err) {
        err.style.display = 'none';
        err.textContent = '';
    }
    const form = document.getElementById('bulk-edit-companies-form');
    if (form) form.reset();
    const modal = document.getElementById('modal-bulk-edit-companies');
    if (modal) modal.style.display = 'flex';
}

function closeBulkEditCompaniesModal() {
    const modal = document.getElementById('modal-bulk-edit-companies');
    if (modal) modal.style.display = 'none';
}

async function submitBulkEditCompanies(event) {
    if (event && typeof event.preventDefault === 'function') event.preventDefault();
    const ids = selectedAdminCompanyIds();
    const err = document.getElementById('bulk-edit-companies-error');
    if (!ids.length) {
        if (err) {
            err.style.display = 'block';
            err.textContent = 'Select at least one company.';
        }
        return;
    }
    const status = (document.getElementById('bulk-edit-company-status') || {}).value || '';
    const clientId = (document.getElementById('bulk-edit-company-client') || {}).value || '';
    const packageVal = (document.getElementById('bulk-edit-company-package') || {}).value;
    const payload = { company_ids: ids };
    if (status) payload.status = status;
    if (clientId) payload.client_id = Number(clientId);
    if (packageVal != null && String(packageVal).trim() !== '') payload.package = String(packageVal).trim();
    if (!payload.status && !payload.client_id && !('package' in payload)) {
        if (err) {
            err.style.display = 'block';
            err.textContent = 'Choose at least one field to change.';
        }
        return;
    }
    const submitBtn = document.getElementById('bulk-edit-companies-submit');
    if (submitBtn) submitBtn.disabled = true;
    try {
        const res = await fetch('/api/admin/companies/bulk-update', {
            method: 'POST',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data.status !== 'success') {
            throw new Error(data.message || 'Bulk edit failed.');
        }
        closeBulkEditCompaniesModal();
        clearAdminCompaniesSelection();
        if (typeof loadAdminCompanies === 'function') await loadAdminCompanies();
    } catch (e) {
        if (err) {
            err.style.display = 'block';
            err.textContent = e.message || 'Bulk edit failed.';
        }
    } finally {
        if (submitBtn) submitBtn.disabled = false;
    }
}

let companiesHouseSearchTimer = null;
let portfolioRenameSearchTimer = null;

function clearPortfolioRenameChMatch() {
    const tick = document.getElementById('portfolio-rename-ch-tick');
    const number = document.getElementById('portfolio-rename-ch-number');
    const statusField = document.getElementById('portfolio-rename-ch-status');
    const box = document.getElementById('portfolio-rename-ch-results');
    if (tick) tick.hidden = true;
    if (number) number.value = '';
    if (statusField) statusField.value = '';
    if (box) {
        box.hidden = true;
        box.innerHTML = '';
    }
}

function setPortfolioRenameChMatch(company, options) {
    const opts = options || {};
    const tick = document.getElementById('portfolio-rename-ch-tick');
    const number = document.getElementById('portfolio-rename-ch-number');
    const statusField = document.getElementById('portfolio-rename-ch-status');
    const statusEl = document.getElementById('portfolio-rename-status');
    if (number) number.value = (company && company.company_number) || '';
    if (statusField) statusField.value = (company && company.status) || '';
    if (tick) tick.hidden = !company;
    if (statusEl && company) {
        statusEl.textContent = opts.message || `On record at Companies House (${company.company_number}). Save to link official details.`;
        statusEl.style.color = '#047857';
    }
    if (window.lucide) lucide.createIcons();
}

function searchCompaniesHouseForRename(inputEl) {
    const box = document.getElementById('portfolio-rename-ch-results');
    if (!inputEl || !box) return;
    const query = String(inputEl.value || '').trim();
    clearPortfolioRenameChMatch();
    const statusEl = document.getElementById('portfolio-rename-status');
    window.clearTimeout(portfolioRenameSearchTimer);
    if (query.length < 2) {
        if (statusEl) statusEl.textContent = '';
        return;
    }
    portfolioRenameSearchTimer = window.setTimeout(async () => {
        box.hidden = false;
        box.innerHTML = '<button type="button" class="ch-search-item is-status" disabled>Searching Companies House…</button>';
        if (statusEl) {
            statusEl.textContent = 'Checking Companies House…';
            statusEl.style.color = '#64748b';
        }
        try {
            const res = await fetch(`/api/admin/companies/search?q=${encodeURIComponent(query)}`, { credentials: 'same-origin' });
            const data = await res.json().catch(() => ({}));
            const matches = data.companies || [];
            if (!res.ok || data.status !== 'success') {
                box.innerHTML = `<button type="button" class="ch-search-item is-status" disabled>${escapeHtml(data.message || 'Companies House search is unavailable.')}</button>`;
                if (statusEl) {
                    statusEl.textContent = data.message || 'Companies House search is unavailable.';
                    statusEl.style.color = '#dc2626';
                }
                return;
            }
            const exact = companiesHouseMatchForName(query, matches);
            if (exact) {
                setPortfolioRenameChMatch(exact);
                hideCompaniesHouseResults(inputEl);
                return;
            }
            if (!matches.length) {
                box.innerHTML = '<button type="button" class="ch-search-item is-status" disabled>Not on record at Companies House yet. You can still save the desired application name.</button>';
                if (statusEl) {
                    statusEl.textContent = 'Not on record yet — save keeps this as the desired application name.';
                    statusEl.style.color = '#64748b';
                }
                return;
            }
            box.innerHTML = matches.slice(0, 6).map((company, index) => `
                <button type="button" class="ch-search-item" data-ch-index="${index}">
                    <strong>${escapeHtml(company.name)}</strong>
                    <span>${escapeHtml(company.company_number)}${company.status ? ` · ${escapeHtml(company.status)}` : ''}</span>
                </button>
            `).join('');
            box.querySelectorAll('.ch-search-item[data-ch-index]').forEach((btn) => {
                btn.addEventListener('click', () => {
                    const picked = matches[Number(btn.getAttribute('data-ch-index'))];
                    if (!picked) return;
                    inputEl.value = picked.name || query;
                    setPortfolioRenameChMatch(picked);
                    hideCompaniesHouseResults(inputEl);
                });
            });
            if (statusEl) {
                statusEl.textContent = 'Pick the official Companies House record, or save the desired name if it is not registered yet.';
                statusEl.style.color = '#64748b';
            }
        } catch (err) {
            box.innerHTML = '<button type="button" class="ch-search-item is-status" disabled>Could not reach Companies House.</button>';
            if (statusEl) {
                statusEl.textContent = 'Could not reach Companies House.';
                statusEl.style.color = '#dc2626';
            }
        }
    }, 350);
}

function pendingFormValue(formEl, name) {
    const field = formEl && formEl.querySelector(`[name="${name}"]`);
    return field ? String(field.value || '').trim() : '';
}

function hideCompaniesHouseResults(inputEl) {
    const box = inputEl && inputEl.closest('.ch-search-wrap') && inputEl.closest('.ch-search-wrap').querySelector('.ch-search-results');
    if (box) {
        box.hidden = true;
        box.innerHTML = '';
    }
}

async function searchCompaniesHouse(inputEl) {
    const wrap = inputEl && inputEl.closest('.ch-search-wrap');
    const box = wrap && wrap.querySelector('.ch-search-results');
    if (!box) return;
    const query = String(inputEl.value || '').trim();
    wrap.querySelectorAll('input[type="hidden"]').forEach((field) => { field.value = ''; });
    window.clearTimeout(companiesHouseSearchTimer);
    if (query.length < 2) {
        hideCompaniesHouseResults(inputEl);
        return;
    }
    companiesHouseSearchTimer = window.setTimeout(async () => {
        box.hidden = false;
        box.innerHTML = `<button type="button" class="ch-search-item is-status" disabled>Searching Companies House…</button>`;
        try {
            const res = await fetch(`/api/admin/companies/search?q=${encodeURIComponent(query)}`, { credentials: 'same-origin' });
            const data = await res.json().catch(() => ({}));
            const matches = data.companies || [];
            if (!res.ok || data.status !== 'success') {
                box.innerHTML = `<button type="button" class="ch-search-item is-status" disabled>${escapeHtml(data.message || 'Companies House search is unavailable.')}</button>`;
                return;
            }
            if (!matches.length) {
                box.innerHTML = `<button type="button" class="ch-search-item is-status" disabled>No live Companies House match.</button>`;
                return;
            }
            box.innerHTML = matches.map((company, index) => `
                <button type="button" class="ch-search-item" data-ch-index="${index}">
                    <strong>${escapeHtml(company.name)}</strong>
                    <span>${escapeHtml(company.company_number)}${company.status ? ` · ${escapeHtml(company.status)}` : ''}</span>
                </button>
            `).join('');
            box.querySelectorAll('.ch-search-item[data-ch-index]').forEach((btn) => {
                btn.addEventListener('click', () => chooseCompaniesHouseMatch(inputEl, matches[Number(btn.getAttribute('data-ch-index'))]));
            });
        } catch (err) {
            box.innerHTML = `<button type="button" class="ch-search-item is-status" disabled>Could not reach Companies House.</button>`;
        }
    }, 350);
}

function chooseCompaniesHouseMatch(inputEl, company) {
    const wrap = inputEl && inputEl.closest('.ch-search-wrap');
    const formEl = inputEl && inputEl.form;
    if (!wrap || !formEl || !company) return;
    formEl.elements.company_name.value = company.name || '';
    formEl.elements.company_number.value = company.company_number || '';
    formEl.elements.inc_date.value = company.inc_date || '';
    formEl.elements.company_status.value = company.status || '';
    formEl.elements.reg_office.value = company.reg_office || '';
    hideCompaniesHouseResults(inputEl);
}

async function confirmPendingCompanyName(orderId, formEl) {
    const name = pendingFormValue(formEl, 'company_name');
    if (!orderId || !name) return;
    const button = formEl.querySelector('button[type="submit"]');
    if (button) button.disabled = true;
    try {
        const res = await fetch('/api/admin/companies', {
            method: 'POST',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                order_id: orderId,
                name,
                company_number: pendingFormValue(formEl, 'company_number'),
                inc_date: pendingFormValue(formEl, 'inc_date'),
                status: pendingFormValue(formEl, 'company_status'),
                reg_office: pendingFormValue(formEl, 'reg_office'),
            }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data.status !== 'success') {
            throw new Error(data.message || 'save-failed');
        }
        await loadAdminCompanies();
    } catch (err) {
        if (button) button.disabled = false;
        alert(err.message || 'Could not create the company card.');
    }
}

function switchPortfolioDetailTab(tabName) {
    const modal = document.getElementById('modal-company-portfolio');
    if (!modal) return;
    modal.querySelectorAll('.portfolio-tabs:not(.portfolio-doc-tabs) .portfolio-tab').forEach((btn) => {
        btn.classList.toggle('active', btn.getAttribute('data-tab') === tabName);
    });
    modal.querySelectorAll('.portfolio-tab-panel').forEach((panel) => {
        panel.hidden = panel.getAttribute('data-panel') !== tabName;
    });
}

function switchPortfolioDocumentsTab(tabName) {
    activePortfolioDocumentsTab = tabName === 'posted' ? 'posted' : 'customer';
    document.querySelectorAll('#portfolio-detail-documents .portfolio-doc-tabs .portfolio-tab').forEach((btn) => {
        btn.classList.toggle('active', btn.getAttribute('data-doc-tab') === activePortfolioDocumentsTab);
    });
    document.querySelectorAll('#portfolio-detail-documents .portfolio-doc-panel').forEach((panel) => {
        panel.hidden = panel.getAttribute('data-doc-panel') !== activePortfolioDocumentsTab;
    });
}

function documentPreviewKind(doc) {
    const blob = `${(doc && doc.name) || ''} ${(doc && doc.file_type) || ''}`.toLowerCase();
    if (/\.pdf\b/.test(blob) || blob.includes('pdf')) return 'pdf';
    if (/\.(png|jpe?g|gif|webp|svg|heic|heif|bmp)\b/.test(blob) || blob.includes('image') || blob.includes('png') || blob.includes('jpeg')) return 'image';
    if (/\.txt\b/.test(blob) || blob.includes('text/plain')) return 'text';
    return 'other';
}

function documentActionButtons(doc) {
    const id = Number(doc && doc.id);
    if (!id) return '';
    const nameJs = JSON.stringify(String((doc && doc.name) || 'Document'));
    const typeJs = JSON.stringify(String((doc && doc.file_type) || ''));
    const canDelete = isAdminShellUser(currentUser) && typeof canUploadClientDocuments === 'function' && canUploadClientDocuments();
    const deleteBtn = canDelete
        ? `<button type="button" class="btn-secondary btn-table portfolio-doc-delete-btn" onclick="event.stopPropagation(); deletePortfolioDocument(${id}, ${nameJs})">Delete</button>`
        : '';
    return `
        <div class="table-action-btns">
            <button type="button" class="btn-secondary btn-table" onclick='event.stopPropagation(); openDocumentPreview(${id}, ${nameJs}, ${typeJs})'>View</button>
            <a class="btn-primary btn-table" href="/api/documents/${id}/download" onclick="event.stopPropagation();">Download</a>
            ${deleteBtn}
        </div>
    `;
}

function documentPreviewMeta(name, fileType) {
    const typed = String(fileType || '').trim();
    if (typed) return typed;
    const kind = documentPreviewKind({ name, file_type: fileType });
    if (kind === 'pdf') return 'PDF Document';
    if (kind === 'image') return 'Image';
    if (kind === 'text') return 'Text file';
    return 'Document';
}

const DOCUMENT_PREVIEW_ZOOM_MIN = 0.5;
const DOCUMENT_PREVIEW_ZOOM_MAX = 4;
const DOCUMENT_PREVIEW_ZOOM_STEP = 0.25;
let documentPreviewZoom = 1;
let documentPreviewRotation = 0;
let documentPreviewZoomCtl = null;

function documentPreviewZoomOrigin(body, clientX, clientY) {
    const rect = body.getBoundingClientRect();
    const viewX = clientX - rect.left;
    const viewY = clientY - rect.top;
    return {
        viewX,
        viewY,
        contentX: (body.scrollLeft + viewX) / documentPreviewZoom,
        contentY: (body.scrollTop + viewY) / documentPreviewZoom
    };
}

function measureDocumentPreviewFit(body, inner, kind) {
    const availW = Math.max(1, body.clientWidth);
    const availH = Math.max(1, body.clientHeight);
    if (kind === 'image' && inner && inner.naturalWidth) {
        const nw = inner.naturalWidth;
        const nh = inner.naturalHeight;
        const swapped = documentPreviewRotation % 180 === 90;
        const fitW = swapped ? nh : nw;
        const fitH = swapped ? nw : nh;
        const scale = Math.min(availW / fitW, availH / fitH, 1);
        return {
            w: Math.max(1, nw * scale),
            h: Math.max(1, nh * scale)
        };
    }
    return { w: availW, h: availH };
}

function applyDocumentPreviewZoom(origin) {
    const body = document.getElementById('document-preview-body');
    const stage = body && body.querySelector('.document-preview-zoom-stage');
    const label = document.getElementById('document-preview-zoom-label');
    const outBtn = document.getElementById('document-preview-zoom-out');
    const inBtn = document.getElementById('document-preview-zoom-in');
    if (label) label.textContent = `${Math.round(documentPreviewZoom * 100)}%`;
    if (outBtn) outBtn.disabled = documentPreviewZoom <= DOCUMENT_PREVIEW_ZOOM_MIN + 0.001;
    if (inBtn) inBtn.disabled = documentPreviewZoom >= DOCUMENT_PREVIEW_ZOOM_MAX - 0.001;
    if (!body || !stage) return;
    const z = documentPreviewZoom;
    const rot = ((documentPreviewRotation % 360) + 360) % 360;
    const swapped = rot % 180 === 90;
    const baseW = Number(stage.dataset.baseW) || body.clientWidth;
    const baseH = Number(stage.dataset.baseH) || body.clientHeight;
    const displayW = swapped ? baseH : baseW;
    const displayH = swapped ? baseW : baseH;
    stage.style.width = `${displayW * z}px`;
    stage.style.height = `${displayH * z}px`;
    const inner = stage.firstElementChild;
    if (inner) {
        inner.style.position = 'absolute';
        inner.style.left = `${(displayW * z - baseW) / 2}px`;
        inner.style.top = `${(displayH * z - baseH) / 2}px`;
        inner.style.width = `${baseW}px`;
        inner.style.height = `${baseH}px`;
        inner.style.maxWidth = 'none';
        inner.style.maxHeight = 'none';
        inner.style.transformOrigin = 'center center';
        inner.style.transform = `rotate(${rot}deg) scale(${z})`;
    }
    body.classList.toggle('is-zoomed', z > 1.01);
    if (origin) {
        body.scrollLeft = origin.contentX * z - origin.viewX;
        body.scrollTop = origin.contentY * z - origin.viewY;
    }
}

function setDocumentPreviewZoom(next, origin) {
    const clamped = Math.min(DOCUMENT_PREVIEW_ZOOM_MAX, Math.max(DOCUMENT_PREVIEW_ZOOM_MIN, next));
    documentPreviewZoom = Math.round(clamped * 100) / 100;
    applyDocumentPreviewZoom(origin);
}

function zoomDocumentPreview(direction) {
    const modal = document.getElementById('modal-document-preview');
    const body = document.getElementById('document-preview-body');
    if (!modal || !modal.classList.contains('active') || !body) return;
    const origin = documentPreviewZoomOrigin(body, body.getBoundingClientRect().left + body.clientWidth / 2, body.getBoundingClientRect().top + body.clientHeight / 2);
    setDocumentPreviewZoom(documentPreviewZoom + (direction > 0 ? DOCUMENT_PREVIEW_ZOOM_STEP : -DOCUMENT_PREVIEW_ZOOM_STEP), origin);
}

function rotateDocumentPreview() {
    const modal = document.getElementById('modal-document-preview');
    const body = document.getElementById('document-preview-body');
    if (!modal || !modal.classList.contains('active') || !body || !body.classList.contains('is-image')) return;
    documentPreviewRotation = (documentPreviewRotation + 90) % 360;
    const stage = body.querySelector('.document-preview-zoom-stage');
    const inner = stage && stage.firstElementChild;
    if (stage && inner) {
        const fit = measureDocumentPreviewFit(body, inner, 'image');
        stage.dataset.baseW = String(fit.w);
        stage.dataset.baseH = String(fit.h);
    }
    applyDocumentPreviewZoom();
    safeCreateIcons();
}

function teardownDocumentPreviewZoom() {
    if (documentPreviewZoomCtl) {
        documentPreviewZoomCtl.abort();
        documentPreviewZoomCtl = null;
    }
    documentPreviewZoom = 1;
    documentPreviewRotation = 0;
    const zoomBar = document.getElementById('document-preview-zoom');
    if (zoomBar) zoomBar.hidden = true;
    const label = document.getElementById('document-preview-zoom-label');
    if (label) label.textContent = '100%';
}

function mountDocumentPreviewZoom(body, kind) {
    teardownDocumentPreviewZoom();
    const zoomBar = document.getElementById('document-preview-zoom');
    if (!body || kind !== 'image') {
        if (zoomBar) zoomBar.hidden = true;
        return;
    }
    if (zoomBar) zoomBar.hidden = false;
    documentPreviewZoom = 1;
    const inner = body.firstElementChild;
    if (!inner) return;
    const stage = document.createElement('div');
    stage.className = 'document-preview-zoom-stage';
    inner.replaceWith(stage);
    stage.appendChild(inner);

    const captureFit = () => {
        const fit = measureDocumentPreviewFit(body, inner, kind);
        stage.dataset.baseW = String(fit.w);
        stage.dataset.baseH = String(fit.h);
        applyDocumentPreviewZoom();
    };
    if (kind === 'image') {
        if (inner.complete && inner.naturalWidth) captureFit();
        else inner.addEventListener('load', captureFit, { once: true });
    }

    const ctl = new AbortController();
    documentPreviewZoomCtl = ctl;
    const { signal } = ctl;
    let pinch = null;
    let pan = null;
    let lastTap = 0;
    let ignoreTap = false;

    const touchPoint = (touches, index) => touches.item(index) || touches[index];
    const touchDistance = (a, b) => Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY);
    const touchMid = (a, b) => ({ x: (a.clientX + b.clientX) / 2, y: (a.clientY + b.clientY) / 2 });

    body.addEventListener('touchstart', (event) => {
        if (event.touches.length === 2) {
            const a = touchPoint(event.touches, 0);
            const b = touchPoint(event.touches, 1);
            pinch = { dist: touchDistance(a, b), zoom: documentPreviewZoom };
            pan = null;
            ignoreTap = true;
            event.preventDefault();
        } else if (event.touches.length === 1) {
            const t = touchPoint(event.touches, 0);
            pan = { x: t.clientX, y: t.clientY, sl: body.scrollLeft, st: body.scrollTop };
            pinch = null;
        }
    }, { signal, passive: false });

    body.addEventListener('touchmove', (event) => {
        if (pinch && event.touches.length === 2) {
            const a = touchPoint(event.touches, 0);
            const b = touchPoint(event.touches, 1);
            const mid = touchMid(a, b);
            const dist = touchDistance(a, b);
            if (pinch.dist > 0) {
                setDocumentPreviewZoom(pinch.zoom * (dist / pinch.dist), documentPreviewZoomOrigin(body, mid.x, mid.y));
            }
            event.preventDefault();
            return;
        }
        if (pan && event.touches.length === 1) {
            const t = touchPoint(event.touches, 0);
            body.scrollLeft = pan.sl - (t.clientX - pan.x);
            body.scrollTop = pan.st - (t.clientY - pan.y);
            event.preventDefault();
        }
    }, { signal, passive: false });

    body.addEventListener('touchend', (event) => {
        if (event.touches.length < 2) pinch = null;
        if (event.touches.length === 0) {
            const ended = event.changedTouches && event.changedTouches[0];
            const now = Date.now();
            if (!ignoreTap && ended && now - lastTap < 280) {
                const origin = documentPreviewZoomOrigin(body, ended.clientX, ended.clientY);
                setDocumentPreviewZoom(documentPreviewZoom > 1.2 ? 1 : 2, origin);
            }
            lastTap = ignoreTap ? 0 : now;
            ignoreTap = false;
            pan = null;
        }
    }, { signal });

    body.addEventListener('wheel', (event) => {
        if (!event.ctrlKey && !event.metaKey) return;
        event.preventDefault();
        const origin = documentPreviewZoomOrigin(body, event.clientX, event.clientY);
        const delta = event.deltaY > 0 ? -0.1 : 0.1;
        setDocumentPreviewZoom(documentPreviewZoom + delta, origin);
    }, { signal, passive: false });

    body.addEventListener('pointerdown', (event) => {
        if (event.pointerType === 'touch' || event.button !== 0) return;
        pan = { x: event.clientX, y: event.clientY, sl: body.scrollLeft, st: body.scrollTop, id: event.pointerId };
        body.classList.add('is-panning');
        try { body.setPointerCapture(event.pointerId); } catch (err) { /* ignore */ }
    }, { signal });

    body.addEventListener('pointermove', (event) => {
        if (!pan || pan.id !== event.pointerId || event.pointerType === 'touch') return;
        body.scrollLeft = pan.sl - (event.clientX - pan.x);
        body.scrollTop = pan.st - (event.clientY - pan.y);
    }, { signal });

    const endPan = (event) => {
        if (pan && pan.id === event.pointerId) {
            pan = null;
            body.classList.remove('is-panning');
        }
    };
    body.addEventListener('pointerup', endPan, { signal });
    body.addEventListener('pointercancel', endPan, { signal });
    safeCreateIcons();
}

async function sniffDocumentPreviewKind(viewUrl, fallbackKind) {
    try {
        const res = await fetch(viewUrl, {
            credentials: 'same-origin',
            headers: { Range: 'bytes=0-15' }
        });
        if (!res.ok && res.status !== 206) return fallbackKind;
        const buf = new Uint8Array(await res.arrayBuffer());
        const ascii = Array.from(buf.slice(0, 5), (b) => String.fromCharCode(b)).join('');
        if (ascii.startsWith('%PDF')) return 'pdf';
        if (buf[0] === 0xFF && buf[1] === 0xD8) return 'image';
        if (buf[0] === 0x89 && buf[1] === 0x50) return 'image';
        if (ascii.startsWith('GIF8')) return 'image';
        if (ascii.startsWith('RIFF')) return 'image';
    } catch (err) {
        /* keep filename-based kind */
    }
    return fallbackKind;
}

function renderDocumentPreviewBody(body, kind, viewUrl, label) {
    teardownDocumentPreviewZoom();
    body.className = `document-preview-body is-${kind}`;
    if (kind === 'image') {
        body.innerHTML = `<img class="document-preview-image" src="${viewUrl}" alt="${escapeHtml(label)}">`;
        mountDocumentPreviewZoom(body, 'image');
        return;
    }
    if (kind === 'pdf') {
        body.innerHTML = `<iframe class="document-preview-frame" title="${escapeHtml(label)}" src="${viewUrl}#view=FitH" allow="fullscreen"></iframe>`;
        return;
    }
    if (kind === 'text') {
        body.innerHTML = `<iframe class="document-preview-frame" title="${escapeHtml(label)}" src="${viewUrl}"></iframe>`;
        return;
    }
    body.innerHTML = `<div class="document-preview-fallback"><p>This file type can’t be previewed in the portal.</p></div>`;
}

function openDocumentPreview(docId, name, fileType) {
    const id = Number(docId);
    if (!id) return;
    const modal = document.getElementById('modal-document-preview');
    const title = document.getElementById('document-preview-title');
    const body = document.getElementById('document-preview-body');
    const download = document.getElementById('document-preview-download');
    if (!modal || !body) return;
    const label = String(name || 'Document');
    if (title) title.textContent = label;
    if (download) download.href = `/api/documents/${id}/download`;
    const viewUrl = `/api/documents/${id}/view`;
    const guessed = documentPreviewKind({ name: label, file_type: fileType });
    teardownDocumentPreviewZoom();
    body.className = `document-preview-body is-${guessed}`;
    body.innerHTML = '';
    modal.classList.add('active');
    sniffDocumentPreviewKind(viewUrl, guessed).then((kind) => {
        if (!modal.classList.contains('active')) return;
        renderDocumentPreviewBody(body, kind, viewUrl, label);
        safeCreateIcons();
    });
}

function closeDocumentPreview() {
    const modal = document.getElementById('modal-document-preview');
    const body = document.getElementById('document-preview-body');
    teardownDocumentPreviewZoom();
    if (body) {
        body.innerHTML = '';
        body.className = 'document-preview-body';
        body.classList.remove('is-panning', 'is-zoomed');
    }
    if (modal) modal.classList.remove('active');
}

function previewPortfolioDocument(docId, name, fileType) {
    openDocumentPreview(docId, name, fileType);
}

function renderPortfolioDocumentButtons(documents, emptyMessage) {
    const list = Array.isArray(documents) ? documents : [];
    if (!list.length) {
        return `<p class="portfolio-related-empty">${escapeHtml(emptyMessage || 'No documents in this folder yet.')}</p>`;
    }
    return `
        <div class="portfolio-doc-button-grid">
            ${list.map((doc) => {
                const customerUpload = isCustomerUploadedDocument(doc);
                const metaParts = [
                    doc.category || doc.file_type || 'Document',
                    doc.lifecycle_status || '',
                    doc.file_size || '',
                ].filter(Boolean);
                const meta = customerUpload
                    ? `${escapeHtml(doc.status || 'Pending Review')} · ${escapeHtml(metaParts.join(' · '))}`
                    : escapeHtml(metaParts.join(' · '));
                return `
                    <div class="portfolio-doc-button-card" data-document-id="${Number(doc.id) || ''}">
                        <div class="portfolio-doc-button-copy">
                            <strong>${escapeHtml(doc.name || 'Document')}</strong>
                            <span>${meta}</span>
                        </div>
                        <div class="doc-file-actions">
                            ${documentActionButtons(doc)}
                        </div>
                    </div>
                `;
            }).join('')}
        </div>
    `;
}

async function deletePortfolioDocument(docId, docName) {
    const id = Number(docId);
    if (!id) return;
    if (!isAdminShellUser(currentUser)) {
        alert('You do not have permission to delete documents.');
        return;
    }
    const label = docName || 'this document';
    if (!confirm(`Delete ${label}? This removes the file from the portal.`)) return;
    try {
        const res = await fetch(`/api/admin/documents/${id}`, {
            method: 'DELETE',
            credentials: 'same-origin',
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data.status !== 'success') {
            throw new Error(data.message || 'Could not delete document.');
        }
        document.querySelectorAll(`[data-document-id="${id}"]`).forEach((el) => el.remove());
        if (portfolioDetailCache && Array.isArray(portfolioDetailCache.documents)) {
            portfolioDetailCache.documents = portfolioDetailCache.documents.filter((d) => Number(d.id) !== id);
            fillCompanyPortfolioModal(portfolioDetailCache);
            switchPortfolioDetailTab('documents');
            switchPortfolioDocumentsTab(activePortfolioDocumentsTab);
        }
        if (window.lucide) lucide.createIcons();
    } catch (err) {
        alert(err.message || 'Could not delete document.');
    }
}

function setPortfolioText(id, value) {
    const el = document.getElementById(id);
    if (el) el.textContent = value || '—';
}

function fillCompanyPortfolioModal(payload) {
    portfolioDetailCache = payload || {};
    const company = payload.company || {};
    const office = payload.registered_office || {};
    setPortfolioText('portfolio-detail-name', company.name || 'Company');
    setPortfolioText('portfolio-detail-number', company.company_number || '');
    setPortfolioText('portfolio-detail-address', portfolioRegisteredAddressText(company));
    setPortfolioText('portfolio-detail-name-field', company.name || '—');
    const renameForm = document.getElementById('portfolio-rename-form');
    const renameHint = document.getElementById('portfolio-rename-hint');
    const renameInput = document.getElementById('portfolio-rename-input');
    const renameStatus = document.getElementById('portfolio-rename-status');
    const nameField = document.getElementById('portfolio-detail-name-field');
    const saveBtn = document.getElementById('portfolio-rename-save');
    const canRename = portfolioCompanyCanRename(company);
    const needsChMatch = portfolioCompanyNeedsChMatch(company);
    if (renameForm) renameForm.hidden = !canRename;
    if (renameHint) {
        renameHint.hidden = !canRename;
        if (canRename) {
            renameHint.textContent = needsChMatch
                ? 'Type the correct Companies House name. Save once the green tick appears to pull official details.'
                : 'Type a name to check Companies House. Save once the green tick appears, or save the desired application name if it is not registered yet.';
        }
    }
    if (nameField) nameField.hidden = false;
    if (renameInput) {
        renameInput.value = company.name || '';
        renameInput.disabled = !canRename;
    }
    if (saveBtn) saveBtn.disabled = !canRename;
    if (renameStatus) {
        renameStatus.textContent = '';
        renameStatus.style.color = '';
    }
    if (!canRename) {
        clearPortfolioRenameChMatch();
    } else if (renameInput && String(renameInput.value || '').trim().length >= 2) {
        searchCompaniesHouseForRename(renameInput);
    } else {
        clearPortfolioRenameChMatch();
    }
    setPortfolioText('portfolio-detail-number-field', company.company_number || '—');
    setPortfolioText('portfolio-detail-status', companyStatusLabel(company));
    setPortfolioText('portfolio-detail-country', companyCountryLabel(company));
    setPortfolioText('portfolio-detail-inc', formatUkNumericDate(company.inc_date));
    const ownerLabel = document.getElementById('portfolio-detail-owner-label');
    const directors = portfolioDirectorNames(company);
    if (ownerLabel) ownerLabel.textContent = directors.length > 1 ? 'Directors' : 'Director';
    setPortfolioText('portfolio-detail-director', directors.length ? directors.join(' · ') : '—');
    const emailWrap = document.getElementById('portfolio-detail-email-wrap');
    const emailValue = String(company.registered_email || '').trim();
    const isEmailVerified = company.business_email_verified === true;
    if (emailWrap) {
        if (isAdminShellUser(currentUser)) {
            emailWrap.innerHTML = `
                <div class="portfolio-inline-edit">
                    <input type="email" id="portfolio-company-notify-email" class="apple-input" value="${escapeHtml(emailValue)}" placeholder="company@email.com" autocomplete="off">
                    <button type="button" class="btn-primary btn-tiny" onclick="savePortfolioCompanyNotifyEmail(false)">Save</button>
                    <button type="button" class="btn-primary btn-tiny" onclick="savePortfolioCompanyNotifyEmail(true)">${isEmailVerified ? 'Re-verify' : 'Verify'}</button>
                    ${isEmailVerified ? '<button type="button" class="btn-secondary btn-tiny" onclick="unverifyPortfolioCompanyNotifyEmail()">Unverify</button>' : ''}
                </div>
                <span class="portfolio-lock-chip">${isEmailVerified ? 'Verified' : 'Unverified'}</span>
                <span id="portfolio-company-notify-status" class="portfolio-inline-status" role="status"></span>
            `;
        } else {
            emailWrap.innerHTML = `
                <div class="portfolio-locked-value">
                    <strong id="portfolio-detail-email">${escapeHtml(emailValue || '—')}</strong>
                    ${emailValue ? `<span class="portfolio-lock-chip">${isEmailVerified ? 'Verified' : 'Unverified'}</span>` : ''}
                </div>
            `;
        }
    }
    const waCta = document.getElementById('portfolio-compliance-whatsapp-cta');
    if (waCta) {
        const contact = (payload && payload.compliance_contact) || {};
        const waLink = String(contact.whatsapp_link || '').trim();
        const hasAttention = portfolioAttentionIssues(company).length > 0;
        if (waLink && hasAttention) {
            waCta.hidden = false;
            waCta.innerHTML = `<a class="btn-primary btn-tiny" href="${escapeHtml(waLink)}" target="_blank" rel="noopener noreferrer">Contact via WhatsApp</a>`;
        } else {
            waCta.hidden = true;
            waCta.innerHTML = '';
        }
    }
    const histEl = document.getElementById('portfolio-notification-history');
    if (histEl) {
        const rows = Array.isArray(payload.notification_history) ? payload.notification_history : [];
        if (!rows.length) {
            histEl.hidden = true;
            histEl.innerHTML = '';
        } else {
            histEl.hidden = false;
            histEl.innerHTML = `
                <strong style="display:block; margin:10px 0 6px; font-size:12px;">Notification history</strong>
                <ul class="portfolio-notification-history-list">
                    ${rows.slice(0, 12).map((row) => `
                        <li>
                            ${escapeHtml(formatUkNumericDate(String(row.created_at || '').slice(0, 10)) || row.created_at || '')}
                            — ${escapeHtml(row.issue_summary || row.issue_fingerprint || '—')}
                            — ${escapeHtml(row.status || '')}${row.blocked_reason ? ` (${escapeHtml(row.blocked_reason)})` : ''}
                            ${row.engagement ? ` — <em>${escapeHtml(row.engagement)}</em>` : ''}
                        </li>
                    `).join('')}
                </ul>
            `;
        }
    }
    const waWrap = document.getElementById('portfolio-detail-whatsapp-wrap');
    const waValue = String(company.whatsapp_number || '').trim();
    if (waWrap) {
        if (waValue) {
            waWrap.innerHTML = `
                <div class="portfolio-locked-value">
                    <strong id="portfolio-detail-whatsapp">${escapeHtml(waValue)}</strong>
                    <span class="portfolio-lock-chip">Locked</span>
                </div>
            `;
        } else if (isAdminShellUser(currentUser)) {
            waWrap.innerHTML = `
                <div class="portfolio-inline-edit">
                    <input type="tel" id="portfolio-company-whatsapp" class="apple-input" value="" placeholder="+44 7…" autocomplete="off">
                    <button type="button" class="btn-primary btn-tiny" onclick="savePortfolioCompanyWhatsApp()">Save</button>
                </div>
                <span id="portfolio-company-whatsapp-status" class="portfolio-inline-status" role="status"></span>
            `;
        } else {
            waWrap.innerHTML = `<strong id="portfolio-detail-whatsapp">—</strong>`;
        }
    }
    const attentionBox = document.getElementById('portfolio-detail-attention');
    if (attentionBox) {
        const attention = portfolioAttentionIssues(company);
        if (!attention.length) {
            attentionBox.hidden = true;
            attentionBox.innerHTML = '';
        } else {
            attentionBox.hidden = false;
            attentionBox.innerHTML = `
                <strong>Attention required at Companies House</strong>
                <ul>${attention.map((item) => `<li>${escapeHtml(item.title || item.code)}${item.due ? ` — due ${escapeHtml(formatUkNumericDate(item.due))}` : ''}</li>`).join('')}</ul>
            `;
        }
    }
    const officeAddress = office.address || portfolioRegisteredAddressText(company);
    setPortfolioText('portfolio-detail-office', officeAddress === '——————' ? '—' : (officeAddress || '—'));
    setPortfolioText('portfolio-detail-postcode', office.postcode || '—');
    const countryBadge = document.getElementById('portfolio-detail-country-badge');
    if (countryBadge) {
        countryBadge.className = 'portfolio-badge-uk';
        countryBadge.innerHTML = '<span aria-hidden="true">🇬🇧</span> UK';
    }
    const statusBadge = document.getElementById('portfolio-detail-status-badge');
    if (statusBadge) {
        statusBadge.className = `portfolio-badge-status${isCompanyActive(company) ? '' : ' is-pending'}`;
        statusBadge.textContent = companyStatusLabel(company);
    }
    const activity = document.getElementById('portfolio-detail-activity');
    if (activity) {
        const items = Array.isArray(company.sic_activities) ? company.sic_activities.filter((item) => item && item.code) : [];
        if (!items.length) {
            activity.innerHTML = '<p class="portfolio-empty-copy">No SIC or business activity details on file.</p>';
        } else {
            activity.innerHTML = `
                <div class="portfolio-detail-grid portfolio-apple-group">
                    ${items.map((item) => `
                        <div class="full">
                            <span>SIC ${escapeHtml(item.code)}</span>
                            <strong>${escapeHtml(item.description || 'Business activity on Companies House')}</strong>
                        </div>
                    `).join('')}
                </div>
            `;
        }
    }

    const deadlines = Array.isArray(company.deadlines) ? company.deadlines : [];
    const complianceEl = document.getElementById('portfolio-detail-compliance');
    if (complianceEl) {
        const canEditCompliance = isAdminShellUser(currentUser);
        const utrValue = company.utr_number || '';
        const authValue = company.authentication_code || '';
        const activationValue = company.activation_code || '';
        const identityValue = company.identity_verified || 'Not started';
        const pscValue = company.psc_verified || 'Not started';
        const fieldHtml = canEditCompliance ? `
            <form class="portfolio-compliance-form" onsubmit="saveCompanyCompliance(event)">
                <p class="portfolio-empty-copy">Stored on this company card for filings and HMRC / Companies House work.</p>
                <div class="portfolio-detail-grid portfolio-apple-group">
                    <div>
                        <label for="portfolio-compliance-utr">UTR number</label>
                        <input type="text" id="portfolio-compliance-utr" name="utr_number" class="apple-input" maxlength="15" autocomplete="off" value="${escapeHtml(utrValue)}" placeholder="e.g. 1234567890">
                    </div>
                    <div>
                        <label for="portfolio-compliance-auth">Authentication code</label>
                        <input type="text" id="portfolio-compliance-auth" name="authentication_code" class="apple-input" maxlength="12" autocomplete="off" value="${escapeHtml(authValue)}" placeholder="Companies House auth code">
                    </div>
                    <div class="full">
                        <label for="portfolio-compliance-activation">Personal 11 dijits Code</label>
                        <input type="text" id="portfolio-compliance-activation" name="activation_code" class="apple-input" maxlength="40" autocomplete="off" value="${escapeHtml(activationValue)}" placeholder="11-digit personal code">
                    </div>
                    <div>
                        <label for="portfolio-compliance-identity">Identity verification</label>
                        <select id="portfolio-compliance-identity" name="identity_verified" class="apple-input">
                            ${['Not started', 'In progress', 'Verified', 'Failed'].map((status) => `<option value="${status}"${status === identityValue ? ' selected' : ''}>${status}</option>`).join('')}
                        </select>
                    </div>
                    <div>
                        <label for="portfolio-compliance-psc">PSC verification</label>
                        <select id="portfolio-compliance-psc" name="psc_verified" class="apple-input">
                            ${['Not started', 'In progress', 'Verified', 'Update required'].map((status) => `<option value="${status}"${status === pscValue ? ' selected' : ''}>${status}</option>`).join('')}
                        </select>
                    </div>
                </div>
                <div class="portfolio-compliance-actions">
                    <button type="submit" class="btn-primary btn-tiny" id="portfolio-compliance-save">Save codes</button>
                    <span class="portfolio-compliance-status" id="portfolio-compliance-status" role="status"></span>
                </div>
            </form>
        ` : `
            <div class="portfolio-detail-grid portfolio-apple-group">
                <div><span>UTR number</span><strong>${escapeHtml(utrValue || '—')}</strong></div>
                <div><span>Identity verification</span><strong>${escapeHtml(identityValue)}</strong></div>
                <div><span>PSC verification</span><strong>${escapeHtml(pscValue)}</strong></div>
            </div>
        `;
        const rows = [];
        if (company.account_status) {
            rows.push(`<div class="portfolio-related-row"><div><span>Standing</span><strong>${escapeHtml(company.account_status)}</strong></div></div>`);
        }
        deadlines.forEach((item) => {
            rows.push(`<div class="portfolio-related-row"><div><span>${escapeHtml(item.label)}</span><strong>${escapeHtml(formatUkNumericDate(item.date))}</strong></div></div>`);
        });
        const deadlineHtml = rows.length
            ? `<div class="portfolio-related-list portfolio-apple-group">${rows.join('')}</div>`
            : `<p class="portfolio-related-empty">No upcoming deadlines</p>`;
        complianceEl.innerHTML = `${fieldHtml}${deadlineHtml}`;
    }

    const orders = Array.isArray(payload.orders) ? payload.orders : [];
    const ordersEl = document.getElementById('portfolio-detail-orders');
    if (ordersEl) {
        if (!orders.length) {
            ordersEl.innerHTML = `<p class="portfolio-related-empty">No orders linked to this company.</p>`;
        } else {
            ordersEl.innerHTML = `<div class="portfolio-related-list portfolio-apple-group">${orders.map((order) => `
                <div class="portfolio-related-row">
                    <div>
                        <strong>${escapeHtml(order.order_number || 'Order')}</strong>
                        <span>${escapeHtml(order.service_name || '')} · ${escapeHtml(order.status || '')}</span>
                    </div>
                    <button type="button" class="btn-secondary btn-tiny" onclick="openPortfolioCompanyOrder(${Number(order.id)})">View</button>
                </div>
            `).join('')}</div>`;
        }
    }

    const documents = Array.isArray(payload.documents) ? payload.documents : [];
    const docsEl = document.getElementById('portfolio-detail-documents');
    if (docsEl) {
        const customerDocs = documents.filter((doc) => isCustomerUploadedDocument(doc));
        const otherDocs = documents.filter((doc) => !isCustomerUploadedDocument(doc));
        const canUpload = isAdminShellUser(currentUser) && canUploadClientDocuments();
        const uploadHtml = canUpload ? `
            <form class="portfolio-doc-upload" onsubmit="event.preventDefault(); uploadCompanyPostedDocument(this);">
                <p>Upload a document received by post. The customer is emailed and can view it in their portal.</p>
                <div class="portfolio-doc-upload-grid">
                    <input type="text" name="name" required placeholder="Document title (e.g. UTR)" maxlength="120">
                    <select name="category">
                        <option value="Posted Documents" selected>Posted Documents</option>
                        <option value="Certificate of Incorporation">Certificate of Incorporation</option>
                        <option value="Memorandum & Articles">Memorandum &amp; Articles</option>
                        <option value="Share Certificate">Share Certificate</option>
                        <option value="Bank statement">Bank statement</option>
                        <option value="Company Documents">Company Documents</option>
                    </select>
                </div>
                <input type="file" name="file" required accept=".pdf,.png,.jpg,.jpeg,.doc,.docx,.zip">
                <textarea name="client_message" rows="2" placeholder="Optional note for the customer"></textarea>
                <div class="portfolio-doc-upload-actions">
                    <button type="submit" class="btn-primary btn-tiny">Upload and notify customer</button>
                    <span class="portfolio-doc-upload-status" role="status"></span>
                </div>
            </form>
        ` : '';
        docsEl.innerHTML = `
            <div class="portfolio-tabs portfolio-doc-tabs" role="tablist" aria-label="Company documents">
                <button type="button" class="portfolio-tab${activePortfolioDocumentsTab === 'customer' ? ' active' : ''}" data-doc-tab="customer" onclick="switchPortfolioDocumentsTab('customer')">Customer uploads (${customerDocs.length})</button>
                <button type="button" class="portfolio-tab${activePortfolioDocumentsTab === 'posted' ? ' active' : ''}" data-doc-tab="posted" onclick="switchPortfolioDocumentsTab('posted')">Posted documents (${otherDocs.length})</button>
            </div>
            <div class="portfolio-doc-panel" data-doc-panel="customer"${activePortfolioDocumentsTab === 'customer' ? '' : ' hidden'}>
                ${renderPortfolioDocumentButtons(customerDocs, 'No customer uploads or Smart Intake files linked to this company yet.')}
            </div>
            <div class="portfolio-doc-panel" data-doc-panel="posted"${activePortfolioDocumentsTab === 'posted' ? '' : ' hidden'}>
                ${renderPortfolioDocumentButtons(otherDocs, 'No posted or staff documents for this company yet.')}
                ${uploadHtml}
            </div>
        `;
    }

    const deleteBtn = document.getElementById('portfolio-delete-company-btn');
    if (deleteBtn) {
        const companyId = Number((payload.company && payload.company.id) || 0);
        const showDelete = isAdminShellUser(currentUser) && canDeleteCompanies() && companyId;
        deleteBtn.style.display = showDelete ? 'inline-flex' : 'none';
        deleteBtn.setAttribute('data-company-id', companyId || '');
        deleteBtn.setAttribute('data-company-name', (payload.company && payload.company.name) || 'Company');
    }
}

async function openCompanyPortfolioDetail(companyId, options) {
    const requestedId = Number(companyId);
    if (!requestedId) return;
    const stayOnTab = options && options.tab;
    const modal = document.getElementById('modal-company-portfolio');
    const cachedSource = isAdminShellUser(currentUser) ? adminCompaniesCache : clientCompaniesCache;
    const cached = (cachedSource || []).find((item) => Number(item.id) === requestedId);
    if (cached) fillCompanyPortfolioModal({ company: cached, registered_office: { address: cached.reg_office, postcode: '' }, orders: [], documents: [], tickets: [] });
    if (modal) modal.classList.add('active');
    switchPortfolioDetailTab(stayOnTab || 'overview');
    try {
        const detailUrl = isAdminShellUser(currentUser)
            ? `/api/admin/companies/${requestedId}`
            : `/api/client/companies/${requestedId}`;
        const res = await fetch(detailUrl, { credentials: 'same-origin' });
        const data = await res.json();
        if (!res.ok || data.status !== 'success' || !data.company) {
            throw new Error('not-found');
        }
        fillCompanyPortfolioModal(data);
        if (stayOnTab) switchPortfolioDetailTab(stayOnTab);
        if (window.lucide) lucide.createIcons();
    } catch (err) {
        if (modal) modal.classList.remove('active');
    }
}

async function savePendingCompanyName(event) {
    event.preventDefault();
    if (!isAdminShellUser(currentUser)) return;
    const company = portfolioDetailCompanySnapshot();
    const companyId = Number(company.id);
    const statusEl = document.getElementById('portfolio-rename-status');
    const saveBtn = document.getElementById('portfolio-rename-save');
    if (!companyId) {
        if (statusEl) {
            statusEl.textContent = 'Company not found.';
            statusEl.style.color = '#dc2626';
        }
        return;
    }
    if (!portfolioCompanyCanRename(company)) {
        if (statusEl) {
            statusEl.textContent = 'Only staff can update company names.';
            statusEl.style.color = '#dc2626';
        }
        return;
    }
    const needsChMatch = portfolioCompanyNeedsChMatch(company);
    const input = document.getElementById('portfolio-rename-input');
    const name = ((input && input.value) || '').trim();
    const chNumber = ((document.getElementById('portfolio-rename-ch-number') || {}).value || '').trim();
    if (needsChMatch && !chNumber) {
        if (statusEl) {
            statusEl.textContent = 'Select the official Companies House record first (green tick).';
            statusEl.style.color = '#dc2626';
        }
        if (input) searchCompaniesHouseForRename(input);
        return;
    }
    if (!name) {
        if (statusEl) {
            statusEl.textContent = 'Enter the company name.';
            statusEl.style.color = '#dc2626';
        }
        return;
    }
    const payload = { name };
    if (chNumber) payload.companies_house_number = chNumber;
    if (statusEl) {
        statusEl.textContent = 'Saving…';
        statusEl.style.color = '';
    }
    if (saveBtn) saveBtn.disabled = true;
    try {
        const res = await fetch(`/api/admin/companies/${companyId}`, {
            method: 'PUT',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data.status !== 'success' || !data.company) {
            throw new Error(data.message || 'Could not save the company name.');
        }
        portfolioDetailCache = { ...(portfolioDetailCache || {}), company: { ...company, ...data.company } };
        fillCompanyPortfolioModal(portfolioDetailCache);
        if (typeof loadAdminCompanies === 'function' && isAdminShellUser(currentUser)) {
            await loadAdminCompanies();
        }
        const ok = document.getElementById('portfolio-rename-status');
        if (ok) {
            ok.textContent = 'Saved.';
            ok.style.color = '#047857';
        }
    } catch (err) {
        if (statusEl) {
            statusEl.textContent = err.message || 'Could not save the company name.';
            statusEl.style.color = '#dc2626';
        }
    } finally {
        if (saveBtn) saveBtn.disabled = false;
    }
}

async function saveCompanyCompliance(event) {
    event.preventDefault();
    if (!isAdminShellUser(currentUser)) return;
    const companyId = Number(portfolioDetailCache && portfolioDetailCache.company && portfolioDetailCache.company.id);
    if (!companyId) return;
    const statusEl = document.getElementById('portfolio-compliance-status');
    const saveBtn = document.getElementById('portfolio-compliance-save');
    const payload = {
        utr_number: ((document.getElementById('portfolio-compliance-utr') || {}).value || '').trim(),
        authentication_code: ((document.getElementById('portfolio-compliance-auth') || {}).value || '').trim(),
        activation_code: ((document.getElementById('portfolio-compliance-activation') || {}).value || '').trim(),
        identity_verified: ((document.getElementById('portfolio-compliance-identity') || {}).value || 'Not started').trim(),
        psc_verified: ((document.getElementById('portfolio-compliance-psc') || {}).value || 'Not started').trim(),
    };
    if (statusEl) statusEl.textContent = 'Saving…';
    if (saveBtn) saveBtn.disabled = true;
    try {
        const res = await fetch(`/api/admin/companies/${companyId}`, {
            method: 'PUT',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data.status !== 'success' || !data.company) {
            throw new Error(data.message || 'Could not save compliance codes.');
        }
        portfolioDetailCache = { ...(portfolioDetailCache || {}), company: { ...((portfolioDetailCache && portfolioDetailCache.company) || {}), ...data.company } };
        fillCompanyPortfolioModal(portfolioDetailCache);
        switchPortfolioDetailTab('compliance');
        const ok = document.getElementById('portfolio-compliance-status');
        if (ok) {
            ok.textContent = 'Saved.';
            ok.style.color = '#047857';
        }
    } catch (err) {
        if (statusEl) {
            statusEl.textContent = err.message || 'Could not save compliance codes.';
            statusEl.style.color = '#dc2626';
        }
    } finally {
        if (saveBtn) saveBtn.disabled = false;
    }
}

async function savePortfolioCompanyNotifyEmail(verify = false) {
    if (!isAdminShellUser(currentUser)) return;
    const companyId = Number(portfolioDetailCache && portfolioDetailCache.company && portfolioDetailCache.company.id);
    if (!companyId) return;
    const input = document.getElementById('portfolio-company-notify-email');
    const statusEl = document.getElementById('portfolio-company-notify-status');
    const registered_email = ((input && input.value) || '').trim();
    if (!registered_email) {
        if (statusEl) {
            statusEl.textContent = 'Enter a verified business email.';
            statusEl.style.color = '#dc2626';
        }
        return;
    }
    if (statusEl) {
        statusEl.textContent = verify ? 'Saving & verifying…' : 'Saving…';
        statusEl.style.color = '#64748b';
    }
    try {
        const body = { registered_email };
        if (verify) body.verify_business_email = true;
        const res = await fetch(`/api/admin/companies/${companyId}`, {
            method: 'PUT',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data.status !== 'success' || !data.company) {
            throw new Error(data.message || 'Could not save company email.');
        }
        portfolioDetailCache = {
            ...(portfolioDetailCache || {}),
            company: { ...((portfolioDetailCache && portfolioDetailCache.company) || {}), ...data.company },
        };
        if (typeof openCompanyPortfolioDetail === 'function') {
            await openCompanyPortfolioDetail(companyId);
        } else {
            fillCompanyPortfolioModal(portfolioDetailCache);
        }
    } catch (err) {
        if (statusEl) {
            statusEl.textContent = err.message || 'Could not save company email.';
            statusEl.style.color = '#dc2626';
        }
    }
}

async function unverifyPortfolioCompanyNotifyEmail() {
    if (!isAdminShellUser(currentUser)) return;
    const companyId = Number(portfolioDetailCache && portfolioDetailCache.company && portfolioDetailCache.company.id);
    if (!companyId) return;
    const statusEl = document.getElementById('portfolio-company-notify-status');
    try {
        const res = await fetch(`/api/admin/companies/${companyId}`, {
            method: 'PUT',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ unverify_business_email: true }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data.status !== 'success' || !data.company) {
            throw new Error(data.message || 'Could not unverify email.');
        }
        await openCompanyPortfolioDetail(companyId);
    } catch (err) {
        if (statusEl) {
            statusEl.textContent = err.message || 'Could not unverify email.';
            statusEl.style.color = '#dc2626';
        }
    }
}

async function savePortfolioCompanyWhatsApp() {
    if (!isAdminShellUser(currentUser)) return;
    const companyId = Number(portfolioDetailCache && portfolioDetailCache.company && portfolioDetailCache.company.id);
    if (!companyId) return;
    if (String((portfolioDetailCache.company && portfolioDetailCache.company.whatsapp_number) || '').trim()) {
        return;
    }
    const input = document.getElementById('portfolio-company-whatsapp');
    const statusEl = document.getElementById('portfolio-company-whatsapp-status');
    const whatsapp_number = ((input && input.value) || '').trim();
    if (!whatsapp_number) {
        if (statusEl) {
            statusEl.textContent = 'Enter a WhatsApp number.';
            statusEl.style.color = '#dc2626';
        }
        return;
    }
    if (statusEl) {
        statusEl.textContent = 'Saving…';
        statusEl.style.color = '#64748b';
    }
    try {
        const res = await fetch(`/api/admin/companies/${companyId}`, {
            method: 'PUT',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ whatsapp_number }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data.status !== 'success' || !data.company) {
            throw new Error(data.message || 'Could not save WhatsApp number.');
        }
        portfolioDetailCache = {
            ...(portfolioDetailCache || {}),
            company: { ...((portfolioDetailCache && portfolioDetailCache.company) || {}), ...data.company },
        };
        fillCompanyPortfolioModal(portfolioDetailCache);
    } catch (err) {
        if (statusEl) {
            statusEl.textContent = err.message || 'Could not save WhatsApp number.';
            statusEl.style.color = '#dc2626';
        }
    }
}

function closeCompanyPortfolioDetail() {
    const modal = document.getElementById('modal-company-portfolio');
    if (modal) modal.classList.remove('active');
}

function formatPostedDocumentUploadStatus(data) {
    const parts = ['Uploaded to the customer portal.'];
    if (data.notification_created) parts.push('Portal notification created.');
    if (data.email_sent) {
        parts.push(`Email sent to ${data.client_email || 'the customer'}.`);
    } else if (data.email_status) {
        parts.push(`Email not sent (${data.email_status}). Fix SMTP in System Settings or try port 465.`);
    }
    if (data.portal_login_ready === false) {
        parts.push('This customer cannot sign in yet. Set a portal password under Customers / Signups.');
    }
    return parts.join(' ');
}

async function uploadCompanyPostedDocument(formEl) {
    if (!formEl || !canUploadClientDocuments()) return;
    const company = (portfolioDetailCache && portfolioDetailCache.company) || {};
    const companyId = Number(company.id);
    const clientId = Number(portfolioDetailCache && portfolioDetailCache.client_id);
    const statusEl = formEl.querySelector('.portfolio-doc-upload-status');
    const submitBtn = formEl.querySelector('button[type="submit"]');
    const fileInput = formEl.querySelector('input[name="file"]');
    const nameInput = formEl.querySelector('input[name="name"]');
    const categoryInput = formEl.querySelector('[name="category"]');
    const messageInput = formEl.querySelector('[name="client_message"]');
    const file = fileInput && fileInput.files ? fileInput.files[0] : null;
    const name = ((nameInput && nameInput.value) || (file && file.name) || '').trim();
    if (!companyId || !file || !name) {
        if (statusEl) statusEl.textContent = 'Choose a file and enter a document name.';
        return;
    }
    if (submitBtn) submitBtn.disabled = true;
    if (statusEl) statusEl.textContent = 'Uploading…';
    try {
        const b64 = await readFileAsBase64(file);
        const payload = {
            company_id: companyId,
            name,
            file_name: file.name,
            category: (categoryInput && categoryInput.value) || 'Posted Documents',
            client_message: (messageInput && messageInput.value) || 'This document was received by post and uploaded to your company file.',
            file_content_base64: b64,
        };
        if (clientId) payload.client_id = clientId;
        const linkedOrder = Array.isArray(portfolioDetailCache.orders) ? portfolioDetailCache.orders[0] : null;
        if (linkedOrder && linkedOrder.id) payload.order_id = Number(linkedOrder.id);
        const res = await fetch('/api/admin/documents', {
            method: 'POST',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data.status !== 'success') {
            throw new Error(data.message || 'Upload failed.');
        }
        if (statusEl) statusEl.textContent = formatPostedDocumentUploadStatus(data);
        formEl.reset();
        await openCompanyPortfolioDetail(companyId, { tab: 'documents' });
    } catch (err) {
        if (statusEl) statusEl.textContent = err.message || 'Upload failed.';
    } finally {
        if (submitBtn) submitBtn.disabled = false;
    }
}

function openPortfolioCompanyOrder(orderId) {
    closeCompanyPortfolioDetail();
    openOrderDetailsModal(orderId);
}

function renderDashboardCompanies(companies) {
    const grid = document.getElementById('dash-companies-grid');
    const section = document.getElementById('portal-home-companies');
    if (!grid) return;
    const list = (Array.isArray(companies) ? companies : []).slice(0, 4);
    if (!list.length) {
        if (section) section.hidden = true;
        grid.innerHTML = '';
        return;
    }
    if (section) section.hidden = false;
    grid.innerHTML = list.map((company) => {
        const status = company.status || company.account_status || '—';
        const country = company.country || companyCountryLabel(company);
        return `
            <button type="button" class="portal-home-row" onclick="switchView('client-companies')">
                <div>
                    <div class="portal-home-row-title">${escapeHtml(company.name || 'Company')}</div>
                    <div class="portal-home-row-meta">${escapeHtml(company.company_number || '—')}</div>
                </div>
                <div class="portal-home-row-meta">${escapeHtml(country)}</div>
                <span class="status-badge ${dashStatusClass(status)}">${escapeHtml(status)}</span>
                <span class="portal-home-row-meta">View</span>
            </button>
        `;
    }).join('');
}

function renderClientDashDocuments(documents) {
    const grid = document.getElementById('dash-recent-documents');
    const section = document.getElementById('portal-home-documents');
    if (!grid) return;
    const list = (Array.isArray(documents) ? documents : []).slice(0, 4);
    clientDashDocsById = {};
    list.forEach((doc) => {
        if (doc && doc.id) clientDashDocsById[doc.id] = doc;
    });
    if (!list.length) {
        if (section) section.hidden = true;
        grid.innerHTML = '';
        return;
    }
    if (section) section.hidden = false;
    grid.innerHTML = list.map((doc) => {
        const related = doc.company_name || doc.order_number || doc.category || '';
        const nameJs = JSON.stringify(String(doc.name || 'Document'));
        const typeJs = JSON.stringify(String(doc.file_type || ''));
        return `
            <button type="button" class="portal-home-row" onclick='openDocumentPreview(${Number(doc.id)}, ${nameJs}, ${typeJs})'>
                <div>
                    <div class="portal-home-row-title">${escapeHtml(doc.name || 'Document')}</div>
                    <div class="portal-home-row-meta">${escapeHtml(related)}</div>
                </div>
                <div class="portal-home-row-meta">${escapeHtml(formatDate(doc.created_at) || '')}</div>
                <span class="status-badge ${documentStatusClass(doc.status)}">${escapeHtml(doc.status || '—')}</span>
                <span class="portal-home-row-meta">View</span>
            </button>
        `;
    }).join('');
}

async function loadClientCompanies() {
    const grid = document.getElementById('portfolio-companies-grid');
    const countPill = document.getElementById('portfolio-company-count');
    try {
        if (countPill) countPill.textContent = '0 UK';
        if (grid) grid.innerHTML = portfolioSkeletonHtml();
        const res = await fetch('/api/client/companies', { credentials: 'same-origin' });
        const data = await res.json();
        if (!res.ok || data.status !== 'success') {
            throw new Error('load-failed');
        }
        clientCompaniesCache = data.companies || [];
        clientPendingCache = data.pending_registrations || [];
        renderPortfolioCompanies(clientCompaniesCache, 'portfolio-companies-grid', 'portfolio-company-count', clientPendingCache);
    } catch (err) {
        clientCompaniesCache = [];
        clientPendingCache = [];
        if (countPill) countPill.textContent = '0 UK';
        if (grid) {
            grid.innerHTML = `
                <div class="portfolio-error" role="alert">
                    <h3>Unable to load your Business Portfolio. Please try again.</h3>
                </div>
            `;
        }
    }
}

async function loadAdminCompanies() {
    const grid = document.getElementById('admin-portfolio-companies-grid');
    const countPill = document.getElementById('admin-portfolio-company-count');
    try {
        if (countPill) countPill.textContent = '0 UK';
        if (grid) grid.innerHTML = portfolioSkeletonHtml();
        let res = await fetch('/api/admin/companies', { credentials: 'same-origin' });
        let data = await res.json().catch(() => ({}));
        if (!res.ok || data.status !== 'success') {
            res = await fetch('/api/client/companies', { credentials: 'same-origin' });
            data = await res.json().catch(() => ({}));
        }
        const list = (data.companies || []).filter((c) => {
            const st = String((c && c.status) || '').toLowerCase();
            return !(st.includes('dissolved') || st.includes('liquidation') || st === 'closed' || st.includes('converted-closed'));
        });
        if (countPill) countPill.textContent = `${data.total_uk || list.length} UK`;
        const chBanner = document.getElementById('admin-ch-banner');
        if (chBanner) chBanner.hidden = Boolean(data.companies_house_configured);
        adminCompanyClientsCache = Array.isArray(data.clients) ? data.clients : [];
        adminCompaniesCache = list;
        adminPendingCache = data.pending_registrations || [];
        clearAdminCompaniesSelection();
        renderPortfolioCompanies(list, 'admin-portfolio-companies-grid', 'admin-portfolio-company-count', adminPendingCache);
    } catch (err) {
        if (countPill) countPill.textContent = '0 UK';
        if (grid) {
            grid.innerHTML = `
                <div class="portfolio-empty" role="status">
                    <i data-lucide="building-2" style="width:40px; height:40px; color:var(--brand-secondary);"></i>
                    <h3>No companies found.</h3>
                </div>
            `;
            if (window.lucide) lucide.createIcons();
        }
    }
}

function fillCreateCompanyClientOptions() {
    const select = document.getElementById('create-company-client');
    if (!select) return;
    const current = select.value;
    const options = ['<option value="">Select client</option>'].concat(
        adminCompanyClientsCache.map((client) => `<option value="${Number(client.id)}">${escapeHtml(client.full_name || client.email || 'Client')}</option>`)
    );
    select.innerHTML = options.join('');
    if (current && adminCompanyClientsCache.some((client) => String(client.id) === String(current))) {
        select.value = current;
    }
}

function createCompanyClientMode() {
    const checked = document.querySelector('input[name="create-company-client-mode"]:checked');
    return checked && checked.value === 'new' ? 'new' : 'existing';
}

function syncCreateCompanyClientMode() {
    const mode = createCompanyClientMode();
    const existingWrap = document.getElementById('create-company-existing-client');
    const newWrap = document.getElementById('create-company-new-client');
    const clientSelect = document.getElementById('create-company-client');
    if (existingWrap) existingWrap.hidden = mode === 'new';
    if (newWrap) newWrap.hidden = mode !== 'new';
    if (clientSelect) clientSelect.required = mode === 'existing';
}

function companyHouseStatusToSelectValue(status, selectId) {
    const raw = String(status || 'active').trim().toLowerCase();
    const map = {
        active: 'Active',
        dissolved: 'Dissolved',
        liquidation: 'Liquidation',
        'converted-closed': 'Dissolved',
        closed: 'Dissolved',
    };
    const mapped = map[raw] || 'Active';
    const select = document.getElementById(selectId || 'create-company-status');
    if (select && Array.from(select.options).some((option) => option.value === mapped)) {
        return mapped;
    }
    return 'Active';
}

function applyCompaniesHouseMatchToCreateForm(match) {
    if (!match) return;
    const setVal = (id, value) => {
        const el = document.getElementById(id);
        if (el && value !== undefined && value !== null && value !== '') el.value = value;
    };
    setVal('create-company-name', match.name);
    setVal('create-company-number', match.company_number);
    setVal('create-company-inc', match.inc_date);
    setVal('create-company-status', companyHouseStatusToSelectValue(match.status, 'create-company-status'));
    setVal('create-company-office', match.reg_office);
}

function openCreateCompanyModal() {
    if (!isAdminShellUser(currentUser)) return;
    fillCreateCompanyClientOptions();
    if (!adminCompanyClientsCache.length) {
        fetch('/api/admin/companies', { credentials: 'same-origin' })
            .then((res) => res.json().catch(() => ({})))
            .then((data) => {
                if (data.status === 'success' && Array.isArray(data.clients)) {
                    adminCompanyClientsCache = data.clients;
                    fillCreateCompanyClientOptions();
                }
            })
            .catch(() => {});
    }
    const form = document.getElementById('create-company-form');
    if (form) form.reset();
    const existingMode = document.querySelector('input[name="create-company-client-mode"][value="existing"]');
    if (existingMode) existingMode.checked = true;
    syncCreateCompanyClientMode();
    const pkg = document.getElementById('create-company-package');
    if (pkg) pkg.value = 'Historical Registration';
    const err = document.getElementById('create-company-error');
    if (err) {
        err.style.display = 'none';
        err.textContent = '';
    }
    hideCompaniesHouseResults(document.getElementById('create-company-ch-search'));
    const modal = document.getElementById('modal-create-company');
    if (modal) modal.classList.add('active');
    if (window.lucide) lucide.createIcons();
}

function closeCreateCompanyModal() {
    const modal = document.getElementById('modal-create-company');
    if (modal) modal.classList.remove('active');
}

async function searchCreateCompanyHouse(inputEl) {
    const wrap = inputEl && inputEl.closest('.ch-search-wrap');
    const box = wrap && wrap.querySelector('.ch-search-results');
    if (!box) return;
    const query = String(inputEl.value || '').trim();
    window.clearTimeout(companiesHouseSearchTimer);
    if (query.length < 2) {
        hideCompaniesHouseResults(inputEl);
        return;
    }
    companiesHouseSearchTimer = window.setTimeout(async () => {
        box.hidden = false;
        box.innerHTML = `<button type="button" class="ch-search-item is-status" disabled>Searching Companies House…</button>`;
        try {
            const res = await fetch(`/api/admin/companies/search?q=${encodeURIComponent(query)}`, { credentials: 'same-origin' });
            const data = await res.json().catch(() => ({}));
            const matches = data.companies || [];
            if (!res.ok || data.status !== 'success') {
                box.innerHTML = `<button type="button" class="ch-search-item is-status" disabled>${escapeHtml(data.message || 'Companies House search is unavailable.')}</button>`;
                return;
            }
            if (!matches.length) {
                box.innerHTML = `<button type="button" class="ch-search-item is-status" disabled>No live Companies House match.</button>`;
                return;
            }
            box.innerHTML = matches.map((company, index) => `
                <button type="button" class="ch-search-item" data-ch-index="${index}">
                    <strong>${escapeHtml(company.name)}</strong>
                    <span>${escapeHtml(company.company_number)}${company.status ? ` · ${escapeHtml(company.status)}` : ''}</span>
                </button>
            `).join('');
            box.querySelectorAll('.ch-search-item[data-ch-index]').forEach((btn) => {
                btn.addEventListener('click', () => {
                    applyCompaniesHouseMatchToCreateForm(matches[Number(btn.getAttribute('data-ch-index'))] || {});
                    hideCompaniesHouseResults(inputEl);
                });
            });
        } catch (err) {
            box.innerHTML = `<button type="button" class="ch-search-item is-status" disabled>Could not reach Companies House.</button>`;
        }
    }, 350);
}

function importWebfilingClientMode() {
    const checked = document.querySelector('input[name="import-webfiling-client-mode"]:checked');
    return checked && checked.value === 'new' ? 'new' : 'existing';
}

function syncImportWebfilingClientMode() {
    const mode = importWebfilingClientMode();
    const existingWrap = document.getElementById('import-webfiling-existing-client');
    const newWrap = document.getElementById('import-webfiling-new-client');
    const clientSelect = document.getElementById('import-webfiling-client');
    if (existingWrap) existingWrap.hidden = mode === 'new';
    if (newWrap) newWrap.hidden = mode !== 'new';
    if (clientSelect) clientSelect.required = mode === 'existing';
}

function fillImportWebfilingClientOptions() {
    const select = document.getElementById('import-webfiling-client');
    if (!select) return;
    const current = select.value;
    const options = ['<option value="">Select client</option>'].concat(
        adminCompanyClientsCache.map((client) => `<option value="${Number(client.id)}">${escapeHtml(client.full_name || client.email || 'Client')}</option>`)
    );
    select.innerHTML = options.join('');
    if (current && adminCompanyClientsCache.some((client) => String(client.id) === String(current))) {
        select.value = current;
    }
}

function renderImportWebfilingResults(results) {
    const wrap = document.getElementById('import-webfiling-results');
    const body = document.getElementById('import-webfiling-results-body');
    if (!wrap || !body) return;
    const rows = Array.isArray(results) ? results : [];
    if (!rows.length) {
        wrap.hidden = true;
        body.innerHTML = '';
        return;
    }
    body.innerHTML = rows.map((row) => `
        <div class="import-webfiling-result-row">
            <span class="import-webfiling-result-badge ${escapeHtml(row.status || 'error')}">${escapeHtml(row.status || 'error')}</span>
            <div>
                <strong>${escapeHtml(row.name || row.company_number || '—')}</strong>
                <div style="color:#64748b; margin-top:2px;">${escapeHtml(row.company_number || '')}${row.message ? ` · ${escapeHtml(row.message)}` : ''}</div>
            </div>
            <span style="color:#64748b;">${row.company_id ? `#${Number(row.company_id)}` : ''}</span>
        </div>
    `).join('');
    wrap.hidden = false;
}

function openImportWebfilingModal() {
    if (!isAdminShellUser(currentUser)) return;
    fillImportWebfilingClientOptions();
    if (!adminCompanyClientsCache.length) {
        fetch('/api/admin/companies', { credentials: 'same-origin' })
            .then((res) => res.json().catch(() => ({})))
            .then((data) => {
                if (data.status === 'success' && Array.isArray(data.clients)) {
                    adminCompanyClientsCache = data.clients;
                    fillImportWebfilingClientOptions();
                }
            })
            .catch(() => {});
    }
    const form = document.getElementById('import-webfiling-form');
    if (form) form.reset();
    const existingMode = document.querySelector('input[name="import-webfiling-client-mode"][value="existing"]');
    if (existingMode) existingMode.checked = true;
    syncImportWebfilingClientMode();
    const err = document.getElementById('import-webfiling-error');
    const ok = document.getElementById('import-webfiling-success');
    if (err) {
        err.style.display = 'none';
        err.textContent = '';
    }
    if (ok) {
        ok.style.display = 'none';
        ok.textContent = '';
    }
    renderImportWebfilingResults([]);
    const modal = document.getElementById('modal-import-webfiling');
    if (modal) modal.classList.add('active');
    if (window.lucide) lucide.createIcons();
}

function closeImportWebfilingModal() {
    const modal = document.getElementById('modal-import-webfiling');
    if (modal) modal.classList.remove('active');
}

async function submitImportWebfilingForm(event) {
    event.preventDefault();
    const err = document.getElementById('import-webfiling-error');
    const ok = document.getElementById('import-webfiling-success');
    const submitBtn = document.getElementById('import-webfiling-submit');
    const clientMode = importWebfilingClientMode();
    const payload = {
        client_mode: clientMode,
        company_numbers: ((document.getElementById('import-webfiling-numbers') || {}).value || '').trim(),
        update_existing: Boolean((document.getElementById('import-webfiling-update-existing') || {}).checked),
    };
    if (clientMode === 'new') {
        payload.new_client_full_name = ((document.getElementById('import-webfiling-new-name') || {}).value || '').trim();
        payload.new_client_email = ((document.getElementById('import-webfiling-new-email') || {}).value || '').trim();
    } else {
        payload.client_id = Number((document.getElementById('import-webfiling-client') || {}).value);
    }
    if (err) {
        err.style.display = 'none';
        err.textContent = '';
    }
    if (ok) {
        ok.style.display = 'none';
        ok.textContent = '';
    }
    if (!payload.company_numbers) {
        if (err) {
            err.style.display = 'block';
            err.textContent = 'Paste at least one company number.';
        }
        return;
    }
    if (clientMode === 'existing' && !payload.client_id) {
        if (err) {
            err.style.display = 'block';
            err.textContent = 'Select the client these companies belong to.';
        }
        return;
    }
    if (clientMode === 'new' && (!payload.new_client_full_name || !payload.new_client_email)) {
        if (err) {
            err.style.display = 'block';
            err.textContent = 'Enter the new client name and email.';
        }
        return;
    }
    if (submitBtn) submitBtn.disabled = true;
    try {
        const res = await fetch('/api/admin/companies/import-webfiling', {
            method: 'POST',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data.status !== 'success') {
            throw new Error(data.message || 'Import failed.');
        }
        if (ok) {
            ok.style.display = 'block';
            ok.textContent = data.message || 'Import complete.';
        }
        renderImportWebfilingResults(data.results || []);
        adminCompanyClientsCache = [];
        await loadAdminCompanies();
        if (clientMode === 'new' && activeView === 'admin-customers') {
            await loadAdminCustomers();
        }
    } catch (ex) {
        if (err) {
            err.style.display = 'block';
            err.textContent = ex.message || 'Import failed.';
        }
    } finally {
        if (submitBtn) submitBtn.disabled = false;
    }
}

async function submitCreateCompanyForm(event) {
    event.preventDefault();
    const err = document.getElementById('create-company-error');
    const submitBtn = document.getElementById('create-company-submit');
    const clientMode = createCompanyClientMode();
    const payload = {
        client_mode: clientMode,
        name: ((document.getElementById('create-company-name') || {}).value || '').trim(),
        company_number: ((document.getElementById('create-company-number') || {}).value || '').trim(),
        inc_date: ((document.getElementById('create-company-inc') || {}).value || '').trim(),
        status: ((document.getElementById('create-company-status') || {}).value || 'Active').trim(),
        director: ((document.getElementById('create-company-director') || {}).value || '').trim(),
        package: ((document.getElementById('create-company-package') || {}).value || '').trim(),
        reg_office: ((document.getElementById('create-company-office') || {}).value || '').trim(),
    };
    if (clientMode === 'new') {
        payload.new_client_full_name = ((document.getElementById('create-company-new-name') || {}).value || '').trim();
        payload.new_client_email = ((document.getElementById('create-company-new-email') || {}).value || '').trim();
        payload.new_client_phone = ((document.getElementById('create-company-new-phone') || {}).value || '').trim();
    } else {
        payload.client_id = Number((document.getElementById('create-company-client') || {}).value);
    }
    if (err) {
        err.style.display = 'none';
        err.textContent = '';
    }
    if (!payload.name) {
        if (err) {
            err.style.display = 'block';
            err.textContent = 'Enter the company name.';
        }
        return;
    }
    if (clientMode === 'existing' && !payload.client_id) {
        if (err) {
            err.style.display = 'block';
            err.textContent = 'Select the client this company belongs to.';
        }
        return;
    }
    if (clientMode === 'new' && (!payload.new_client_full_name || !payload.new_client_email)) {
        if (err) {
            err.style.display = 'block';
            err.textContent = 'Enter the new client name and email.';
        }
        return;
    }
    if (submitBtn) submitBtn.disabled = true;
    try {
        const res = await fetch('/api/admin/companies', {
            method: 'POST',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data.status !== 'success') {
            throw new Error(data.message || 'Could not add the company.');
        }
        closeCreateCompanyModal();
        adminCompanyClientsCache = [];
        await loadAdminCompanies();
        if (clientMode === 'new' && activeView === 'admin-customers') {
            await loadAdminCustomers();
        }
    } catch (ex) {
        if (err) {
            err.style.display = 'block';
            err.textContent = ex.message || 'Could not add the company.';
        }
    } finally {
        if (submitBtn) submitBtn.disabled = false;
    }
}

async function loadClientAddresses() {
    try {
        const res = await fetch('/api/client/addresses');
        const data = await res.json();
        const tbody = document.getElementById('addresses-table-body');
        if (tbody && data.status === 'success') {
            tbody.innerHTML = data.addresses.map(a => `
                <tr>
                    <td style="font-weight:700; color:var(--brand-primary);">${a.type}</td>
                    <td>${a.line1}, ${a.city}, ${a.postal_code}</td>
                    <td>${a.company_name || 'Personal'}</td>
                    <td>${a.start_date}</td>
                    <td>${a.expiry_date}</td>
                    <td><span class="status-badge completed">${a.status}</span></td>
                </tr>
            `).join('');
        }
    } catch (err) { console.error(err); }
}

async function loadClientInvoices() {
    try {
        const res = await fetch('/api/client/invoices');
        const data = await res.json();
        const tbody = document.getElementById('invoices-table-body');
        if (tbody && data.status === 'success') {
            tbody.innerHTML = data.invoices.map(i => `
                <tr>
                    <td style="font-weight:700;">${i.invoice_number}</td>
                    <td>${i.service_name}</td>
                    <td style="font-weight:800;">£${parseFloat(i.total).toFixed(2)}</td>
                    <td>${i.due_date}</td>
                    <td><span class="status-badge ${invoiceStatusBadgeClass(invoiceDisplayStatus(i))}">${escapeHtml(invoiceDisplayStatus(i))}</span></td>
                    <td><a class="btn-secondary" style="padding:4px 10px; font-size:0.75rem; text-decoration:none;" href="${escapeHtml(i.document_url || `/api/client/invoices/${i.id}/document`)}" target="_blank" rel="noopener">View invoice</a></td>
                </tr>
            `).join('');
        }
    } catch (err) { console.error(err); }
}

async function loadClientPayments() {
    try {
        const res = await fetch('/api/client/payments');
        const data = await res.json();
        const tbody = document.getElementById('payments-table-body');
        if (tbody && data.status === 'success') {
            tbody.innerHTML = data.payments.map(p => `
                <tr>
                    <td style="font-weight:700; color:#006cff;">#PAY-${p.id}</td>
                    <td style="font-weight:600;">${p.service_name}</td>
                    <td>${p.order_number}</td>
                    <td style="font-weight:800; color:#0f172a;">£${parseFloat(p.total).toFixed(2)}</td>
                    <td><span class="badge" style="background:#f1f5f9; color:#475569; padding:4px 8px; border-radius:6px; font-weight:600;">${p.payment_method || 'Credit Card (Stripe)'}</span></td>
                    <td><span class="status-badge completed">${p.status === 'Paid' ? 'Completed' : p.status}</span></td>
                    <td><button class="btn-secondary" style="padding:4px 10px; font-size:0.75rem;">Receipt</button></td>
                </tr>
            `).join('');
        }
    } catch (err) { console.error(err); }
}

async function loadClientServices() {
    try {
        const res = await fetch('/api/client/services');
        const data = await res.json();
        const grid = document.getElementById('services-cards-grid');
        if (grid && data.status === 'success') {
            grid.innerHTML = data.services.map(s => `
                <div class="modal-card" style="margin:0; display:flex; flex-direction:column; justify-content:space-between;">
                    <div>
                        <div style="display:flex; justify-content:space-between; align-items:center;">
                            <span class="badge" style="background:#e0f2fe; color:#0369a1; font-weight:700; font-size:0.75rem;">${escapeHtml(s.category)}</span>
                            <span style="font-size:1.25rem; font-weight:800; color:#003971;">£${parseFloat(s.price).toFixed(2)}</span>
                        </div>
                        <h3 style="font-size:1.1rem; font-weight:700; margin:12px 0 6px 0; color:#0f172a;">${escapeHtml(s.name)}</h3>
                        <p style="font-size:0.85rem; color:#64748b; margin-bottom:16px;">${escapeHtml(s.description || '')}</p>
                    </div>
                    <button type="button" class="btn-primary" style="width:100%; justify-content:center;" onclick="openManualOrderModal({ service_name: '${escapeJsString(s.name)}', price: ${s.price || 0} })"><i data-lucide="shopping-cart"></i> Order Service</button>
                </div>
            `).join('');
            safeCreateIcons();
        }
    } catch (err) { console.error(err); }
}

async function loadClientMessages() {
    try {
        const res = await fetch('/api/client/messages');
        const data = await res.json();
        const threadsList = document.getElementById('threads-list');
        const messagesBody = document.getElementById('chat-messages-body');
        
        if (threadsList && data.status === 'success') {
            if (data.messages.length === 0) {
                threadsList.innerHTML = '<p style="font-size:0.85rem; color:#64748b;">No messages yet.</p>';
            } else {
                threadsList.innerHTML = data.messages.map(m => `
                    <div style="padding:10px; border-bottom:1px solid #f1f5f9; cursor:pointer; background:#f8fafc; border-radius:6px; margin-bottom:6px;">
                        <div style="font-size:0.8rem; font-weight:700; color:#006cff;">${m.ticket_number}</div>
                        <div style="font-size:0.85rem; font-weight:600; color:#0f172a;">${m.subject}</div>
                        <div style="font-size:0.75rem; color:#64748b;">${m.sender_name} • ${formatDate(m.created_at)}</div>
                    </div>
                `).join('');
                
                messagesBody.innerHTML = data.messages.map(m => `
                    <div class="chat-bubble ${m.sender_role.toLowerCase() === 'client' ? 'client' : 'staff'}" style="padding:10px 14px; border-radius:8px; background:${m.sender_role.toLowerCase() === 'client' ? '#003971' : '#f1f5f9'}; color:${m.sender_role.toLowerCase() === 'client' ? '#fff' : '#0f172a'}; max-width:80%; align-self:${m.sender_role.toLowerCase() === 'client' ? 'flex-end' : 'flex-start'};">
                        <div style="font-size:0.75rem; opacity:0.8; margin-bottom:4px;">${m.sender_name} (${m.sender_role}) • ${formatDate(m.created_at)}</div>
                        <div style="font-size:0.9rem;">${m.message}</div>
                    </div>
                `).join('');
            }
        }
    } catch (err) { console.error(err); }
}

async function loadClientDocuments() {
    try {
        const res = await fetch('/api/client/documents');
        const data = await res.json();
        const tbody = document.getElementById('documents-table-body');
        if (tbody && data.status === 'success') {
            tbody.innerHTML = data.documents.map(d => `
                <tr>
                    <td style="font-weight:700;">${escapeHtml(d.name)}</td>
                    <td>${escapeHtml(d.category || d.file_type || '—')}</td>
                    <td>${escapeHtml(formatDate(d.created_at))}</td>
                    <td>${escapeHtml(d.uploaded_by || '—')}</td>
                    <td><span class="status-badge ${documentStatusClass(d.status)}">${escapeHtml(d.status || '—')}</span></td>
                    <td>
                        <div class="doc-file-actions">
                            ${documentActionButtons(d)}
                        </div>
                    </td>
                </tr>
            `).join('');
        }
    } catch (err) { console.error(err); }
}

function openUploadDocumentModal() {
    if (!currentUser || currentUser.role !== 'CLIENT') {
        switchView('client-documents');
        return;
    }
    const err = document.getElementById('client-upload-error');
    if (err) {
        err.style.display = 'none';
        err.textContent = '';
    }
    const form = document.getElementById('client-upload-document-form');
    if (form) form.reset();
    const modal = document.getElementById('modal-client-upload-document');
    if (modal) modal.classList.add('active');
    safeCreateIcons();
}

function closeUploadDocumentModal() {
    const modal = document.getElementById('modal-client-upload-document');
    if (modal) modal.classList.remove('active');
}

async function submitClientUploadDocument(event) {
    if (event) event.preventDefault();
    if (!currentUser || currentUser.role !== 'CLIENT') return;
    const err = document.getElementById('client-upload-error');
    const nameInput = document.getElementById('client-upload-name');
    const fileInput = document.getElementById('client-upload-file');
    const submitBtn = document.getElementById('client-upload-submit');
    const file = fileInput && fileInput.files && fileInput.files[0];
    const name = (nameInput && nameInput.value || '').trim() || (file && file.name) || '';
    if (!file || !name) {
        if (err) {
            err.textContent = 'Choose a file and enter a document name.';
            err.style.display = 'block';
        }
        return;
    }
    try {
        if (submitBtn) submitBtn.disabled = true;
        const b64 = await readFileAsBase64(file);
        const res = await fetch('/api/client/documents/upload', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'same-origin',
            body: JSON.stringify({
                name,
                category: 'Company Documents',
                file_name: file.name,
                file_content_base64: b64
            })
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data.status !== 'success') {
            throw new Error(data.message || 'Unable to upload the document.');
        }
        closeUploadDocumentModal();
        if (activeView === 'client-documents') loadClientDocuments();
        if (activeView === 'client-dashboard') loadClientDashboard();
    } catch (ex) {
        if (err) {
            err.textContent = ex.message || 'Unable to upload the document.';
            err.style.display = 'block';
        }
    } finally {
        if (submitBtn) submitBtn.disabled = false;
    }
}

async function loadClientTickets() {
    try {
        const res = await fetch('/api/client/tickets');
        const data = await res.json();
        const tbody = document.getElementById('tickets-table-body');
        if (tbody && data.status === 'success') {
            tbody.innerHTML = data.tickets.map(t => `
                <tr>
                    <td style="font-weight:700;">${t.ticket_number}</td>
                    <td>${t.subject}</td>
                    <td>${t.category}</td>
                    <td><span class="status-badge ${t.priority === 'Urgent' ? 'cancelled' : 'pending'}">${t.priority}</span></td>
                    <td><span class="status-badge ${t.status === 'Closed' ? 'completed' : 'processing'}">${t.status}</span></td>
                    <td><button class="btn-primary" style="padding:4px 10px; font-size:0.75rem;" onclick="openChatModal(${t.id}, '${t.subject}', '${t.ticket_number}')">Open Chat</button></td>
                </tr>
            `).join('');
        }
    } catch (err) { console.error(err); }
}

// ----------------------------------------------------
// SUPPORT TICKET LIVE CHAT THREAD
// ----------------------------------------------------
async function openChatModal(ticketId, subject, ticketNum) {
    activeTicketId = ticketId;
    document.getElementById('chat-ticket-subject').textContent = subject;
    document.getElementById('chat-ticket-num').textContent = ticketNum;
    
    await refreshChatMessages();
    document.getElementById('ticket-chat-modal').classList.add('active');
}

async function refreshChatMessages() {
    if (!activeTicketId) return;
    try {
        const res = await fetch(`/api/client/tickets/${activeTicketId}/messages`);
        const data = await res.json();
        const box = document.getElementById('chat-messages-box');
        if (box && data.status === 'success') {
            box.innerHTML = data.messages.map(m => `
                <div class="chat-bubble ${m.sender_role.toLowerCase() === 'client' ? 'client' : 'staff'}">
                    <div class="meta">${m.sender_name} (${m.sender_role}) • ${formatDate(m.created_at)}</div>
                    <div>${m.message}</div>
                </div>
            `).join('');
            box.scrollTop = box.scrollHeight;
        }
    } catch (err) { console.error(err); }
}

async function sendChatMessage() {
    const input = document.getElementById('chat-input');
    const msg = input.value.strip ? input.value.trim() : input.value;
    if (!msg || !activeTicketId) return;
    
    try {
        await fetch(`/api/client/tickets/${activeTicketId}/messages`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ message: msg })
        });
        input.value = '';
        await refreshChatMessages();
    } catch (err) { console.error(err); }
}

function closeChatModal() {
    document.getElementById('ticket-chat-modal').classList.remove('active');
    activeTicketId = null;
}

function openNewTicketModal() {
    document.getElementById('modal-new-ticket').classList.add('active');
}

function closeNewTicketModal() {
    document.getElementById('modal-new-ticket').classList.remove('active');
    document.getElementById('new-ticket-form').reset();
}

async function submitNewTicketForm(e) {
    e.preventDefault();
    const subject = document.getElementById('ticket-subject').value.trim();
    const category = document.getElementById('ticket-category').value;
    const priority = document.getElementById('ticket-priority').value;
    const description = document.getElementById('ticket-message').value.trim();

    if (!subject || !description) return;

    try {
        const res = await fetch('/api/client/tickets', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ subject, category, priority, description })
        });
        const data = await res.json();
        if (data.status === 'success') {
            closeNewTicketModal();
            await loadClientTickets();
        } else {
            alert(data.message || 'Error submitting support ticket.');
        }
    } catch (err) {
        console.error('Error submitting ticket:', err);
    }
}

// ----------------------------------------------------
// ADMIN CMS LOADERS
// ----------------------------------------------------
let adminDashCache = null;
let adminDashOrderRange = 'monthly';
let adminDashBreakdownMode = 'today';
let adminUserStatChart = null;

function scrollAdminDashFeed(dir) {
    const track = document.getElementById('adm-dash-feed');
    if (!track) return;
    track.scrollBy({ left: (dir || 1) * 280, behavior: 'smooth' });
}

function setAdminDashOrderRange(range) {
    adminDashOrderRange = range || 'monthly';
    document.querySelectorAll('[data-dash-range]').forEach((btn) => {
        btn.classList.toggle('is-active', btn.getAttribute('data-dash-range') === adminDashOrderRange);
    });
    if (adminDashCache) renderAdminOrderStatChart(adminDashCache);
}

function setAdminDashBreakdown(mode) {
    adminDashBreakdownMode = mode || 'today';
    document.querySelectorAll('[data-dash-break]').forEach((btn) => {
        btn.classList.toggle('is-active', btn.getAttribute('data-dash-break') === adminDashBreakdownMode);
    });
    if (adminDashCache) renderAdminDashBreakdown(adminDashCache);
}

function adminDashToneClass(tone) {
    const t = String(tone || 'blue').toLowerCase();
    if (['purple', 'red', 'orange', 'blue', 'green'].includes(t)) return t;
    return 'blue';
}

function renderAdminDashFeed(payload) {
    const track = document.getElementById('adm-dash-feed');
    if (!track) return;
    const items = Array.isArray(payload.activity) ? payload.activity : [];
    track.innerHTML = items.map((item) => {
        const tone = adminDashToneClass(item.tone);
        const view = escapeHtml(item.view || 'admin-dashboard');
        return `
        <article class="dash-feed-card" onclick="switchView('${view}')">
            <div class="dash-feed-top">
                <span class="dash-feed-icon tone-${tone}"><i data-lucide="${escapeHtml(item.icon || 'sparkles')}"></i></span>
                <button type="button" class="dash-feed-menu" tabindex="-1" aria-hidden="true">•••</button>
            </div>
            <h4 class="dash-feed-title">${escapeHtml(item.title || 'Activity')}</h4>
            <p class="dash-feed-sub">${escapeHtml(item.subtitle || '')}</p>
            <div class="dash-feed-meta">
                <span>${escapeHtml(item.meta_time || '—')}</span>
                <span>${escapeHtml(item.meta_tag || '')}</span>
                <span>${escapeHtml(item.meta_user || '')}</span>
            </div>
        </article>`;
    }).join('');
    safeCreateIcons();
}

function renderAdminDashBreakdown(payload) {
    const host = document.getElementById('adm-dash-breakdown');
    if (!host) return;
    const stats = payload.stats || {};
    let rows = Array.isArray(payload.breakdown) ? payload.breakdown.slice() : [];
    if (adminDashBreakdownMode === 'today') {
        rows = [
            { label: 'Orders today', value: stats.today_orders || 0, tone: 'purple' },
            { label: 'New clients', value: stats.today_customers || 0, tone: 'red' },
            { label: 'Open tickets', value: stats.open_tickets || 0, tone: 'orange' },
            { label: 'Pending docs', value: stats.pending_documents || 0, tone: 'blue' },
        ];
    } else if (adminDashBreakdownMode === 'month') {
        rows = [
            { label: 'Orders month', value: stats.month_orders || 0, tone: 'purple' },
            { label: 'Clients', value: stats.total_customers || 0, tone: 'red' },
            { label: 'Companies', value: stats.total_companies || 0, tone: 'orange' },
            { label: 'Completed', value: stats.completed_orders || 0, tone: 'blue' },
        ];
    } else if (adminDashBreakdownMode === 'new') {
        rows = [
            { label: 'Pending orders', value: stats.pending_orders || 0, tone: 'purple' },
            { label: 'New clients', value: stats.today_customers || 0, tone: 'red' },
            { label: 'Open tickets', value: stats.open_tickets || 0, tone: 'orange' },
            { label: 'Staff', value: stats.staff_count || 0, tone: 'blue' },
        ];
    }
    const maxVal = Math.max(...rows.map((r) => Number(r.value) || 0), 1);
    host.innerHTML = rows.map((row) => {
        const tone = adminDashToneClass(row.tone);
        const value = Number(row.value) || 0;
        const pct = Math.max(8, Math.round((value / maxVal) * 100));
        return `
        <div class="dash-break-row">
            <div class="dash-break-label"><span class="dash-dot tone-${tone}"></span>${escapeHtml(row.label)}</div>
            <div class="dash-break-bar"><div class="dash-break-fill tone-${tone}" style="width:${pct}%"></div></div>
            <div class="dash-break-val">${value.toLocaleString('en-GB')}</div>
        </div>`;
    }).join('');
}

function renderAdminDashRings(payload) {
    const host = document.getElementById('adm-dash-rings');
    if (!host) return;
    const rings = Array.isArray(payload.rings) ? payload.rings : [];
    const circum = 2 * Math.PI * 36;
    host.innerHTML = rings.map((ring) => {
        const tone = adminDashToneClass(ring.tone);
        const pct = Math.max(0, Math.min(100, Number(ring.pct) || 0));
        const offset = circum - (circum * pct) / 100;
        const view = escapeHtml(ring.view || 'admin-dashboard');
        return `
        <article class="dash-ring-card" onclick="switchView('${view}')">
            <div class="dash-ring-label">${escapeHtml(ring.label || '')}</div>
            <div class="dash-ring">
                <svg viewBox="0 0 80 80" aria-hidden="true">
                    <circle class="dash-ring-track" cx="40" cy="40" r="36"></circle>
                    <circle class="dash-ring-value tone-${tone}" cx="40" cy="40" r="36"
                        style="stroke-dasharray:${circum}; stroke-dashoffset:${offset};"></circle>
                </svg>
                <div class="dash-ring-pct">${pct}%</div>
            </div>
            <div class="dash-ring-num">${Number(ring.value || 0).toLocaleString('en-GB')}</div>
            <span class="dash-ring-link">View All</span>
        </article>`;
    }).join('');
}

function renderAdminDashOps(payload) {
    const list = document.getElementById('adm-dash-ops-list');
    const gaugeNum = document.getElementById('adm-dash-gauge-num');
    const gaugeLabel = document.getElementById('adm-dash-gauge-label');
    const gaugeArc = document.getElementById('adm-dash-gauge-arc');
    const ops = payload.ops || {};
    const stats = payload.stats || {};
    if (list) {
        const rows = [
            { label: 'Grade', value: `${ops.grade || stats.completion_rate || 0}%`, tone: 'purple' },
            { label: 'Pending orders', value: String(ops.pending_orders || 0), tone: 'orange' },
            { label: 'Open tickets', value: String(ops.open_tickets || 0), tone: 'red' },
            { label: 'Docs to review', value: String(ops.pending_documents || 0), tone: 'blue' },
        ];
        if (canViewRevenue() && stats.total_revenue) {
            rows[1] = { label: 'Revenue', value: String(stats.total_revenue), tone: 'green' };
        }
        list.innerHTML = rows.map((row) => `
            <li><span class="dash-dot tone-${adminDashToneClass(row.tone)}"></span>${escapeHtml(row.label)}<strong>${escapeHtml(row.value)}</strong></li>
        `).join('');
    }
    const gaugeValue = Number(ops.gauge_value || 0);
    if (gaugeNum) gaugeNum.textContent = String(gaugeValue);
    if (gaugeLabel) gaugeLabel.textContent = ops.gauge_label || 'Open work';
    if (gaugeArc) {
        const circum = 2 * Math.PI * 46;
        const pct = Math.max(8, Math.min(100, gaugeValue === 0 ? 8 : Math.min(100, 20 + gaugeValue * 4)));
        gaugeArc.style.strokeDasharray = String(circum);
        gaugeArc.style.strokeDashoffset = String(circum - (circum * pct) / 100);
    }
}

function renderAdminOrderStatChart(payload) {
    const canvas = document.getElementById('admin-chart-canvas');
    if (!canvas) return;
    const charts = payload.charts || {};
    let rows = [];
    if (adminDashOrderRange === 'weekly') rows = charts.orders_weekly || [];
    else if (adminDashOrderRange === 'yearly') rows = charts.orders_yearly || [];
    else rows = charts.orders_daily || [];
    const labels = rows.map((r) => r.label || '');
    const values = rows.map((r) => Number(r.cnt) || 0);
    const peak = Math.max(...values, 0);
    const peakIdx = values.indexOf(peak);

    const draw = () => {
        if (typeof Chart !== 'function') {
            drawRevenueFallback(canvas, labels, values);
            return;
        }
        if (adminUserStatChart && typeof adminUserStatChart.destroy === 'function') {
            adminUserStatChart.destroy();
            adminUserStatChart = null;
        }
        if (adminChart && typeof adminChart.destroy === 'function') {
            adminChart.destroy();
            adminChart = null;
        }
        adminUserStatChart = new Chart(canvas, {
            type: 'line',
            data: {
                labels,
                datasets: [{
                    label: 'Orders',
                    data: values,
                    borderColor: '#8b5cf6',
                    backgroundColor: (ctx) => {
                        const chart = ctx.chart;
                        const { ctx: c, chartArea } = chart;
                        if (!chartArea) return 'rgba(139, 92, 246, 0.12)';
                        const grad = c.createLinearGradient(0, chartArea.top, 0, chartArea.bottom);
                        grad.addColorStop(0, 'rgba(139, 92, 246, 0.35)');
                        grad.addColorStop(1, 'rgba(139, 92, 246, 0.02)');
                        return grad;
                    },
                    borderWidth: 2.5,
                    fill: false,
                    tension: 0.1,
                    pointRadius: (ctx) => (ctx.dataIndex === peakIdx && peak > 0 ? 5 : 0),
                    pointHoverRadius: 6,
                    pointBackgroundColor: '#8b5cf6',
                    pointBorderColor: '#ffffff',
                    pointBorderWidth: 2,
                }],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        backgroundColor: '#8b5cf6',
                        titleColor: '#fff',
                        bodyColor: '#fff',
                        padding: 10,
                        cornerRadius: 10,
                        displayColors: false,
                        callbacks: {
                            title: () => '',
                            label: (ctx) => `${Number(ctx.parsed.y || 0).toLocaleString('en-GB')} orders`,
                        },
                    },
                },
                scales: {
                    x: {
                        grid: { display: false },
                        ticks: { color: '#aeaeb2', font: { family: APPLE_UI_FONT, size: 11 } },
                        border: { display: false },
                    },
                    y: {
                        beginAtZero: true,
                        grid: { color: 'rgba(0,0,0,0.04)' },
                        ticks: {
                            color: '#aeaeb2',
                            font: { family: APPLE_UI_FONT, size: 11 },
                            precision: 0,
                        },
                        border: { display: false },
                    },
                },
            },
        });
        adminChart = adminUserStatChart;
    };
    requestAnimationFrame(() => requestAnimationFrame(() => {
        ensureChartJs().then(draw).catch(() => drawRevenueFallback(canvas, labels, values));
    }));
}

let starBarChart = null;
let starDonutChart = null;
let starTrendChart = null;

function switchStarDashTab(tabName) {
    document.querySelectorAll('.star-dash-tabs .star-tab').forEach(b => {
        b.classList.remove('active');
        if (b.textContent.toLowerCase().includes(tabName)) b.classList.add('active');
    });
}

function exportStarDashSummary() {
    window.print();
}

function onStarDashboardFilterChange() {
    loadAdminDashboard();
}

function onStarRevenueRangeChange() {
    loadAdminDashboard();
}

function onStarSalesRangeChange() {
    loadAdminDashboard();
}

function renderStarAdminDashboardCharts(data) {
    const activeUser = getCurrentUser();
    const userNameEl = document.getElementById('star-user-name');
    if (userNameEl && activeUser) {
        userNameEl.textContent = activeUser.full_name || 'Admin';
    }
    const greetingTitle = document.getElementById('star-greeting-title');
    if (greetingTitle) {
        const hour = new Date().getHours();
        const timeOfDay = hour < 12 ? 'Good Morning' : hour < 17 ? 'Good Afternoon' : 'Good Evening';
        const name = activeUser ? (activeUser.full_name || 'Admin') : 'Admin';
        greetingTitle.innerHTML = `${timeOfDay}, <span>${escapeHtml(name)}</span>`;
    }

    const dateLabel = document.getElementById('star-current-date-label');
    if (dateLabel) {
        const d = new Date();
        const month = String(d.getMonth() + 1).padStart(2, '0');
        const day = String(d.getDate()).padStart(2, '0');
        dateLabel.textContent = `${month}/${day}/${d.getFullYear()}`;
    }

    const stats = (data && data.stats) || {};
    const totalSales = stats.total_orders || 0;
    const totalRevRaw = typeof stats.total_revenue === 'number' ? stats.total_revenue : parseFloat(String(stats.total_revenue || '0').replace(/[^0-9.]/g, '') || '0');
    const totalCust = stats.total_customers || 0;
    const avgPrice = totalSales > 0 ? (totalRevRaw / totalSales) : 0;

    const setVal = (id, val) => {
        const el = document.getElementById(id);
        if (el) el.textContent = val;
    };

    setVal('star-metric-sales', totalSales.toLocaleString());
    setVal('star-metric-revenue', `£${totalRevRaw.toFixed(2)}`);
    setVal('star-metric-customers', totalCust.toLocaleString());
    setVal('star-metric-avgprice', `£${avgPrice.toFixed(2)}`);
    setVal('star-metric-turnover', `£${(totalRevRaw * 1.15).toFixed(2)}`);

    if (typeof Chart !== 'function') return;

    // 1. Revenue Analytics (Bar Chart)
    const barCanvas = document.getElementById('star-revenue-bar-canvas');
    if (barCanvas) {
        if (starBarChart) { starBarChart.destroy(); starBarChart = null; }
        const monthly = (data && data.charts && data.charts.monthly) || [];
        const labels = monthly.length ? monthly.map(m => m.label || m.month) : ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
        const values = monthly.length ? monthly.map(m => Number(m.rev) || 0) : [350, 520, 110, 400, 560, 320, 240];

        starBarChart = new Chart(barCanvas, {
            type: 'bar',
            data: {
                labels: labels,
                datasets: [{
                    label: 'Revenue (£)',
                    data: values,
                    backgroundColor: '#2563eb',
                    borderRadius: 6,
                    borderSkipped: false,
                    barThickness: 24
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: { legend: { display: false } },
                scales: {
                    x: { grid: { display: false } },
                    y: { grid: { color: 'rgba(226, 232, 240, 0.6)' }, beginAtZero: true }
                }
            }
        });
    }

    // 2. Sales Analytics (Donut Chart)
    const donutCanvas = document.getElementById('star-sales-donut-canvas');
    if (donutCanvas) {
        if (starDonutChart) { starDonutChart.destroy(); starDonutChart = null; }
        starDonutChart = new Chart(donutCanvas, {
            type: 'doughnut',
            data: {
                labels: ['Incorporation', 'Compliance', 'Address Services'],
                datasets: [{
                    data: [35, 45, 20],
                    backgroundColor: ['#2563eb', '#38bdf8', '#0ea5e9'],
                    borderWidth: 0
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                cutout: '72%',
                plugins: { legend: { display: false } }
            }
        });
    }

    // 3. Sales Trend (Dual Area Chart)
    const areaCanvas = document.getElementById('star-trend-area-canvas');
    if (areaCanvas) {
        if (starTrendChart) { starTrendChart.destroy(); starTrendChart = null; }
        const labels = ['Week 1', 'Week 2', 'Week 3', 'Week 4', 'Week 5', 'Week 6', 'Week 7', 'Week 8'];
        starTrendChart = new Chart(areaCanvas, {
            type: 'line',
            data: {
                labels: labels,
                datasets: [
                    {
                        label: 'Online Payment',
                        data: [180, 220, 310, 280, 390, 420, 510, 480],
                        borderColor: '#2563eb',
                        backgroundColor: 'rgba(37, 99, 235, 0.12)',
                        fill: true,
                        tension: 0.4,
                        pointRadius: 3
                    },
                    {
                        label: 'Offline Sales',
                        data: [120, 150, 260, 210, 300, 340, 410, 390],
                        borderColor: '#38bdf8',
                        backgroundColor: 'rgba(56, 189, 248, 0.12)',
                        fill: true,
                        tension: 0.4,
                        pointRadius: 3
                    }
                ]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: { legend: { display: false } },
                scales: {
                    x: { grid: { display: false } },
                    y: { grid: { color: 'rgba(226, 232, 240, 0.6)' }, beginAtZero: true }
                }
            }
        });
    }
}

async function loadAdminDashboard() {
    if (!canViewAdminDashboard()) return;
    try {
        const res = await fetch('/api/admin/stats', { credentials: 'same-origin' });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data.status !== 'success') {
            throw new Error(data.message || 'Unable to load dashboard.');
        }
        const setText = (id, value) => {
            const el = document.getElementById(id);
            if (el) el.textContent = value;
        };
        setText('adm-tot-cust', data.stats.total_customers);
        setText('adm-tot-ord', data.stats.total_orders);
        setText('adm-tot-tix', data.stats.open_tickets);
        if (canViewRevenue()) {
            setText('adm-tot-rev', data.stats.total_revenue);
            renderAdminChart((data.charts && data.charts.monthly) || []);
        }

        renderStarAdminDashboardCharts(data);
    } catch (err) {
        console.error(err);
    }
}

function renderAdminChart(monthlyData) {
    const canvas = document.getElementById('admin-chart-canvas');
    const empty = document.getElementById('admin-chart-empty');
    if (!canvas) return;
    const rows = Array.isArray(monthlyData) ? monthlyData : [];
    const labels = rows.map((m) => m.label || m.month || '');
    const values = rows.map((m) => {
        const n = Number(m.rev);
        return Number.isFinite(n) ? n : 0;
    });
    if (empty) empty.hidden = values.some((v) => v > 0);
    const draw = () => {
        if (typeof Chart === 'function') {
            if (adminChart && typeof adminChart.destroy === 'function') {
                adminChart.destroy();
                adminChart = null;
            }
            adminChart = new Chart(canvas, {
                type: 'line',
                data: {
                    labels,
                    datasets: [{
                        label: 'Revenue (£)',
                        data: values,
                        borderColor: '#003971',
                        backgroundColor: 'rgba(0, 108, 255, 0.12)',
                        borderWidth: 2,
                        fill: false,
                        tension: 0.1,
                        pointRadius: 4,
                        pointHoverRadius: 6,
                        pointBackgroundColor: '#006cff',
                        pointBorderColor: '#ffffff',
                        pointBorderWidth: 2
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    resizeDelay: 0,
                    font: { family: APPLE_UI_FONT },
                    plugins: {
                        legend: { display: false },
                        tooltip: {
                            titleFont: { family: APPLE_UI_FONT },
                            bodyFont: { family: APPLE_UI_FONT },
                            callbacks: {
                                label(ctx) {
                                    const amount = Number(ctx.parsed.y || 0);
                                    return `£${amount.toLocaleString('en-GB', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
                                }
                            }
                        }
                    },
                    scales: {
                        y: {
                            beginAtZero: true,
                            ticks: {
                                font: { family: APPLE_UI_FONT },
                                callback(value) {
                                    return `£${value}`;
                                }
                            }
                        },
                        x: {
                            ticks: { font: { family: APPLE_UI_FONT } }
                        }
                    }
                }
            });
            return;
        }
        drawRevenueFallback(canvas, labels, values);
    };
    requestAnimationFrame(() => requestAnimationFrame(draw));
}

function drawRevenueFallback(canvas, labels, values) {
    if (!canvas) return;
    const wrap = canvas.parentElement;
    const width = Math.max(wrap ? wrap.clientWidth : 0, 260);
    const height = Math.max(wrap ? wrap.clientHeight : 0, 200);
    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.floor(width * dpr);
    canvas.height = Math.floor(height * dpr);
    canvas.style.width = `${width}px`;
    canvas.style.height = `${height}px`;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);
    const pad = { top: 16, right: 12, bottom: 36, left: 48 };
    const plotW = Math.max(width - pad.left - pad.right, 10);
    const plotH = Math.max(height - pad.top - pad.bottom, 10);
    const max = Math.max(...(values || [1]), 1);
    ctx.strokeStyle = '#e2e8f0';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(pad.left, pad.top);
    ctx.lineTo(pad.left, pad.top + plotH);
    ctx.lineTo(pad.left + plotW, pad.top + plotH);
    ctx.stroke();

    ctx.strokeStyle = '#8b5cf6';
    ctx.lineWidth = 2;
    ctx.beginPath();
    (values || []).forEach((v, i) => {
        const x = pad.left + (i * plotW) / Math.max(values.length - 1, 1);
        const y = pad.top + plotH - (v / max) * plotH;
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
    });
    ctx.stroke();

    ctx.fillStyle = '#8b5cf6';
    (values || []).forEach((v, i) => {
        const x = pad.left + (i * plotW) / Math.max(values.length - 1, 1);
        const y = pad.top + plotH - (v / max) * plotH;
        ctx.beginPath();
        ctx.arc(x, y, 4, 0, Math.PI * 2);
        ctx.fill();
    });
}

let adminOrdersPage = 1;
let adminOrdersTimer = null;

function resetAdminOrdersPage() {
    adminOrdersPage = 1;
}

function scheduleLoadAdminOrders() {
    resetAdminOrdersPage();
    clearTimeout(adminOrdersTimer);
    adminOrdersTimer = setTimeout(loadAdminOrders, 250);
}

function onAdminOrderDatePresetChange() {
    const preset = document.getElementById('filter-admin-order-preset')?.value || '';
    const showCustom = preset === 'custom';
    const fromEl = document.getElementById('filter-admin-order-from');
    const toEl = document.getElementById('filter-admin-order-to');
    [fromEl, toEl].forEach((el) => {
        if (!el) return;
        el.classList.toggle('is-visible', showCustom);
        el.hidden = !showCustom;
        if (!showCustom) el.value = '';
    });
}

function adminOrderProgressRange() {
    const raw = document.getElementById('filter-admin-order-progress')?.value || '';
    if (!raw || !raw.includes('-')) return { min: '', max: '' };
    const [min, max] = raw.split('-');
    return { min, max };
}

let adminOrderClientTypeFilter = 'All';

function setAdminOrderClientTypeFilter(type) {
    adminOrderClientTypeFilter = type;
    ['All', 'Customer', 'B2B'].forEach(t => {
        const btn = document.getElementById(`tab-orders-${t.toLowerCase()}`);
        if (btn) {
            if (t === type) {
                btn.classList.add('active');
                btn.style.background = 'var(--color-primary)';
                btn.style.color = '#FFFFFF';
            } else {
                btn.classList.remove('active');
                btn.style.background = 'var(--color-surface)';
                btn.style.color = 'var(--color-text-primary)';
            }
        }
    });
    resetAdminOrdersPage();
    loadAdminOrders();
}

function buildAdminOrderQuery(page) {
    const qs = new URLSearchParams();
    const search = document.getElementById('filter-admin-order-search')?.value.trim() || '';
    const product = document.getElementById('filter-admin-order-product')?.value || '';
    const category = document.getElementById('filter-admin-order-category')?.value || '';
    const statusGroup = document.getElementById('filter-admin-order-status-group')?.value || '';
    const status = document.getElementById('filter-admin-order-status')?.value || '';
    const payment = document.getElementById('filter-admin-order-payment')?.value || '';
    const customer = document.getElementById('filter-admin-order-customer')?.value || '';
    const preset = document.getElementById('filter-admin-order-preset')?.value || '';
    const month = document.getElementById('filter-admin-order-month')?.value || '';
    const year = document.getElementById('filter-admin-order-year')?.value || '';
    const dateFrom = document.getElementById('filter-admin-order-from')?.value || '';
    const dateTo = document.getElementById('filter-admin-order-to')?.value || '';
    const progress = adminOrderProgressRange();
    if (search) qs.set('search', search);
    if (product) qs.set('product', product);
    if (category) qs.set('category', category);
    if (statusGroup) qs.set('status_group', statusGroup);
    if (status) qs.set('status', status);
    if (canViewRevenue() && payment) qs.set('payment_status', payment);
    if (customer) qs.set('customer_id', customer);
    if (adminOrderClientTypeFilter && adminOrderClientTypeFilter !== 'All') {
        qs.set('client_type', adminOrderClientTypeFilter);
    }
    if (preset && preset !== 'custom') qs.set('date_preset', preset);
    if (preset === 'custom' && dateFrom) qs.set('date_from', dateFrom);
    if (preset === 'custom' && dateTo) qs.set('date_to', dateTo);
    if (!preset && month) qs.set('month', month);
    if (!preset && year) qs.set('year', year);
    if (progress.min !== '') qs.set('progress_min', progress.min);
    if (progress.max !== '') qs.set('progress_max', progress.max);
    qs.set('page', String(page || 1));
    qs.set('limit', '25');
    return qs;
}

function fillAdminOrderFacet(id, values, labelKey, valueKey) {
    const sel = document.getElementById(id);
    if (!sel) return;
    const current = sel.value;
    const first = sel.options[0] ? sel.options[0].outerHTML : '<option value="">All</option>';
    const items = values || [];
    sel.innerHTML = first + items.map((item) => {
        const value = valueKey ? String(item[valueKey]) : String(item);
        const label = labelKey ? item[labelKey] : item;
        return `<option value="${escapeHtml(value)}"${value === current ? ' selected' : ''}>${escapeHtml(label)}</option>`;
    }).join('');
    if (current && ![...sel.options].some((opt) => opt.value === current)) {
        sel.insertAdjacentHTML('beforeend', `<option value="${escapeHtml(current)}" selected>${escapeHtml(current)}</option>`);
    }
}

function renderAdminOrderStats(stats) {
    const s = stats || {};
    const set = (id, value) => {
        const el = document.getElementById(id);
        if (el) el.textContent = value;
    };
    set('adm-ord-stat-total', s.total_orders ?? 0);
    set('adm-ord-stat-pending', s.pending ?? 0);
    set('adm-ord-stat-progress', s.in_progress ?? 0);
    set('adm-ord-stat-completed', s.completed ?? 0);
    if (canViewRevenue() && s.revenue != null) {
        set('adm-ord-stat-revenue', `£${parseFloat(s.revenue || 0).toFixed(2)}`);
    } else {
        set('adm-ord-stat-revenue', '—');
    }
    set('adm-ord-stat-month', s.this_month ?? 0);
}

function renderAdminOrderPagination(pagination) {
    const box = document.getElementById('adm-orders-pagination');
    if (!box) return;
    const totalPages = pagination?.total_pages || 1;
    const page = pagination?.page || 1;
    const total = pagination?.total || 0;
    if (total === 0) {
        box.innerHTML = '';
        return;
    }
    box.innerHTML = `
        <span style="font-size:0.8rem; color:#64748b;">${total} orders · page ${page} of ${totalPages}</span>
        <div style="display:flex; gap:8px;">
            <button type="button" class="btn-secondary" ${page <= 1 ? 'disabled' : ''} onclick="goAdminOrdersPage(${page - 1})">Previous</button>
            <button type="button" class="btn-secondary" ${page >= totalPages ? 'disabled' : ''} onclick="goAdminOrdersPage(${page + 1})">Next</button>
        </div>
    `;
}

function goAdminOrdersPage(page) {
    adminOrdersPage = Math.max(1, page);
    loadAdminOrders();
}

function clearAdminOrderFilters() {
    ['filter-admin-order-search', 'filter-admin-order-product', 'filter-admin-order-category',
     'filter-admin-order-status-group', 'filter-admin-order-status', 'filter-admin-order-payment',
     'filter-admin-order-customer', 'filter-admin-order-progress', 'filter-admin-order-preset',
     'filter-admin-order-month', 'filter-admin-order-year', 'filter-admin-order-from', 'filter-admin-order-to'
    ].forEach((id) => {
        const el = document.getElementById(id);
        if (el) el.value = '';
    });
    onAdminOrderDatePresetChange();
    resetAdminOrdersPage();
    loadAdminOrders();
}

function showAdminOrdersError(message) {
    const box = document.getElementById('adm-orders-error');
    if (!box) return;
    box.style.display = message ? 'block' : 'none';
    box.textContent = message || '';
}

function adminOrdersColspan() {
    return canViewRevenue() ? 10 : 8;
}

async function loadAdminOrders() {
    applyAdminOrderFinanceVisibility();
    const tbody = document.getElementById('adm-orders-table-body');
    showAdminOrdersError('');
    try {
        if (tbody) {
            tbody.innerHTML = `<tr><td colspan="${adminOrdersColspan()}" style="text-align:center; color:#64748b; padding:28px;">Loading orders…</td></tr>`;
        }
        const qs = buildAdminOrderQuery(adminOrdersPage);
        const res = await fetch(`/api/admin/orders?${qs.toString()}`, { credentials: 'same-origin' });
        const data = await res.json();
        if (!res.ok || data.status !== 'success') {
            throw new Error(data.message || 'Unable to load orders.');
        }
        fillAdminOrderFacet('filter-admin-order-product', data.facets?.products || []);
        renderAdminOrderStats(data.stats);
        renderAdminOrderPagination(data.pagination);
        if (!tbody) return;
        if (!data.orders.length) {
            tbody.innerHTML = `<tr><td colspan="${adminOrdersColspan()}" style="text-align:center; color:#64748b; padding:28px;">No orders match the current filters.</td></tr>`;
            syncAdminOrdersSelectAllState();
            return;
        }
        const showFinance = canViewRevenue();
        tbody.innerHTML = data.orders.map(o => {
            const product = o.products_summary || o.service_name || '—';
            const financeCells = showFinance ? `
                    <td class="cell-price">${adminOrderPriceCell(o)}</td>
                    <td class="cell-payment">
                        <select class="table-select" data-order-action="payment_mode" data-order-id="${o.id}">${staffOrderPaymentModeOptions(o.payment_mode || '')}</select>
                    </td>` : '';
            const ownerName = o.owner_name || o.client_name || '—';
            const ownerEmail = o.owner_form_email || '';
            return `
                <tr class="order-row" data-order-id="${o.id}">
                    <td class="cell-select">
                        <input type="checkbox" class="adm-order-select" value="${o.id}" data-order-id="${o.id}" aria-label="Select order ${escapeHtml(o.order_number)}">
                    </td>
                    <td><button type="button" class="order-number-link" data-order-action="open" data-order-id="${o.id}">${escapeHtml(o.order_number)}</button></td>
                    <td class="cell-date">${escapeHtml(formatShortDate(o.created_at))}</td>
                    <td class="cell-client">
                        <div class="owner-cell-name">${escapeHtml(ownerName)}</div>
                        ${ownerEmail ? `<div class="owner-cell-email">${escapeHtml(ownerEmail)}</div>` : ''}
                    </td>
                    <td class="cell-company">${escapeHtml(o.company_name || '—')}</td>
                    <td class="cell-product">${renderProductPills(product, o.category_name)}</td>
                    ${financeCells}
                    <td class="cell-status">
                        <select class="table-select" data-order-action="status" data-order-id="${o.id}">
                            <option value="Pending Verification" ${o.status === 'Pending Verification' ? 'selected' : ''}>Pending Verification</option>
                            <option value="Pending" ${o.status === 'Pending' ? 'selected' : ''}>Pending</option>
                            <option value="Processing" ${o.status === 'Processing' ? 'selected' : ''}>Processing</option>
                            <option value="In Progress" ${o.status === 'In Progress' ? 'selected' : ''}>In Progress</option>
                            <option value="Completed" ${o.status === 'Completed' ? 'selected' : ''}>Completed</option>
                            <option value="Cancelled" ${o.status === 'Cancelled' ? 'selected' : ''}>Cancelled</option>
                            <option value="Refunded" ${o.status === 'Refunded' ? 'selected' : ''}>Refunded</option>
                        </select>
                    </td>
                    <td class="cell-actions">
                        <div class="order-row-actions table-action-btns">
                            <button type="button" class="btn-primary btn-table" data-order-action="open" data-order-id="${o.id}">Manage</button>
                            ${canManageOrders() ? `<button type="button" class="btn-secondary btn-table" data-order-action="change-details" data-order-id="${o.id}">Change details</button>` : ''}
                            ${canDeleteOrders() ? `<button type="button" class="portfolio-delete-btn" data-order-action="delete" data-order-id="${o.id}" data-order-number="${escapeHtml(o.order_number)}" title="Delete order" aria-label="Delete order"><i data-lucide="trash-2"></i></button>` : ''}
                        </div>
                    </td>
                </tr>`;
        }).join('');
        syncAdminOrdersSelectAllState();
        safeCreateIcons();
    } catch (err) {
        showAdminOrdersError(err.message || 'Unable to load orders.');
        if (tbody) {
            tbody.innerHTML = `<tr><td colspan="${adminOrdersColspan()}" style="text-align:center; color:#dc2626; padding:28px;">Unable to load orders.</td></tr>`;
        }
        syncAdminOrdersSelectAllState();
    }
}

function toggleSelectAllAdminOrders(checked) {
    document.querySelectorAll('#adm-orders-table-body input.adm-order-select').forEach((el) => {
        el.checked = !!checked;
    });
    syncAdminOrdersSelectAllState();
}

function syncAdminOrdersSelectAllState() {
    const master = document.getElementById('adm-orders-select-all');
    const boxes = Array.from(document.querySelectorAll('#adm-orders-table-body input.adm-order-select'));
    const selected = boxes.filter((el) => el.checked);
    if (master) {
        if (!boxes.length) {
            master.checked = false;
            master.indeterminate = false;
        } else {
            master.checked = selected.length === boxes.length;
            master.indeterminate = selected.length > 0 && selected.length < boxes.length;
        }
    }
    const bar = document.getElementById('adm-orders-selection-bar');
    const countEl = document.getElementById('adm-orders-selected-count');
    if (countEl) countEl.textContent = String(selected.length);
    if (bar) bar.style.display = selected.length ? 'flex' : 'none';
    const deleteBtn = document.getElementById('adm-orders-delete-selected');
    if (deleteBtn) deleteBtn.style.display = canDeleteOrders() && selected.length ? 'inline-flex' : 'none';
    const editBtn = document.getElementById('adm-orders-edit-selected');
    if (editBtn) editBtn.style.display = selected.length ? 'inline-flex' : 'none';
    if (selected.length) safeCreateIcons();
}

function getSelectedAdminOrderIds() {
    return Array.from(document.querySelectorAll('#adm-orders-table-body input.adm-order-select:checked'))
        .map((el) => Number(el.value))
        .filter((id) => id > 0);
}

function clearSelectedAdminOrders() {
    document.querySelectorAll('#adm-orders-table-body input.adm-order-select').forEach((el) => {
        el.checked = false;
    });
    syncAdminOrdersSelectAllState();
}

function editSelectedAdminOrders() {
    const ids = getSelectedAdminOrderIds();
    if (!ids.length) {
        alert('Select at least one order to edit.');
        return;
    }
    if (ids.length > 1) {
        alert('Select one order at a time to change details, or click Change details on that row.');
        return;
    }
    openStaffOrderWorkspace(ids[0], { editCheckout: true });
}

async function deleteSelectedAdminOrders() {
    if (!canDeleteOrders()) {
        alert('You do not have permission to delete orders.');
        return;
    }
    const ids = getSelectedAdminOrderIds();
    if (!ids.length) {
        alert('Select at least one order to delete.');
        return;
    }
    const label = ids.length === 1 ? 'this order' : `${ids.length} selected orders`;
    if (!confirm(`Permanently delete ${label}? This cannot be undone.`)) return;
    showAdminOrdersError('');
    let failed = 0;
    for (const orderId of ids) {
        try {
            const res = await fetch(`/api/admin/orders/${orderId}`, {
                method: 'DELETE',
                credentials: 'same-origin'
            });
            const data = await res.json().catch(() => ({}));
            if (!res.ok || data.status !== 'success') failed += 1;
        } catch (_) {
            failed += 1;
        }
    }
    if (failed) {
        showAdminOrdersError(`${failed} order(s) could not be deleted.`);
    }
    await loadAdminOrders();
}

function formatOrderPrice(total) {
    const value = parseFloat(total || 0);
    if (!Number.isFinite(value)) return '£0';
    if (Math.abs(value - Math.round(value)) < 0.001) {
        return `£${Math.round(value)}`;
    }
    return `£${value.toFixed(2)}`;
}

function adminOrderPriceCell(order) {
    const amount = parseFloat(order.total || 0).toFixed(2);
    const editBtn = canEditOrderPrice()
        ? `<button type="button" class="icon-edit-btn" data-order-action="edit-price" data-order-id="${order.id}" title="Edit price" aria-label="Edit price"><i data-lucide="pencil"></i></button>`
        : '';
    return `<div class="table-price-wrap" data-price-wrap="${order.id}">
        <span class="table-price-value">${formatOrderPrice(amount)}</span>
        ${editBtn}
    </div>`;
}

function beginAdminOrderPriceEdit(orderId, wrapEl) {
    if (!canEditOrderPrice()) return;
    const wrap = wrapEl || document.querySelector(`[data-price-wrap="${orderId}"]`);
    if (!wrap || wrap.querySelector('input[data-order-action="total"]')) return;
    const current = wrap.querySelector('.table-price-value');
    const raw = current ? String(current.textContent || '').replace(/£/g, '').replace(/,/g, '').trim() : '0.00';
    wrap.innerHTML = `<span>£</span>
        <input type="number" min="0" max="100000" step="0.01" inputmode="decimal" class="table-select table-price-input" data-order-action="total" data-order-id="${orderId}" value="${escapeHtml(raw)}" aria-label="Order price">`;
    const input = wrap.querySelector('input');
    if (input) {
        input.focus();
        input.select();
    }
}

function beginStaffOrderPriceEdit() {
    if (!canEditOrderPrice() || !staffOrderId) return;
    const editor = document.getElementById('staff-order-price-editor');
    const input = document.getElementById('staff-order-price-input');
    const editBtn = document.getElementById('staff-order-price-edit');
    const priceEl = document.getElementById('staff-order-price');
    if (!editor || !input) return;
    input.value = (priceEl && priceEl.dataset.total) || input.value || '0.00';
    editor.hidden = false;
    if (editBtn) editBtn.hidden = true;
    input.focus();
    input.select();
}

async function saveStaffOrderPriceEdit() {
    const input = document.getElementById('staff-order-price-input');
    if (!input || !staffOrderId) return;
    await updateAdminOrderTotal(staffOrderId, input.value, input);
}

async function updateAdminOrderTotal(orderId, rawValue, inputEl) {
    if (!canEditOrderPrice()) {
        alert('Only an administrator can change order prices.');
        if (inputEl) await loadAdminOrders();
        return;
    }
    const parsed = parseFloat(String(rawValue || '').replace(/£/g, '').replace(/,/g, '').trim());
    if (!Number.isFinite(parsed) || parsed < 0 || parsed > 100000) {
        alert('Enter a valid price between £0 and £100,000.');
        if (inputEl) await loadAdminOrders();
        return;
    }
    const total = Math.round(parsed * 100) / 100;
    try {
        const res = await fetch(`/api/admin/orders/${orderId}`, {
            method: 'PUT',
            headers: {'Content-Type': 'application/json'},
            credentials: 'same-origin',
            body: JSON.stringify({ total })
        });
        const data = await res.json();
        if (!res.ok || data.status !== 'success') throw new Error(data.message || 'Unable to update price.');
        if (inputEl) inputEl.value = total.toFixed(2);
        loadAdminOrders();
        if (staffOrderId === orderId) await openStaffOrderWorkspace(orderId);
    } catch (err) {
        alert(err.message || 'Unable to update price.');
        await loadAdminOrders();
    }
}

async function updateAdminOrderPaymentMode(orderId, paymentMode) {
    if (!canViewRevenue()) {
        alert('Only an administrator can change payment mode.');
        return;
    }
    try {
        const res = await fetch(`/api/admin/orders/${orderId}`, {
            method: 'PUT',
            headers: {'Content-Type': 'application/json'},
            credentials: 'same-origin',
            body: JSON.stringify({ payment_mode: paymentMode || null })
        });
        const data = await res.json();
        if (!res.ok || data.status !== 'success') throw new Error(data.message || 'Unable to update payment mode.');
        loadAdminOrders();
        if (staffOrderId === orderId) await openStaffOrderWorkspace(orderId);
    } catch (err) {
        alert(err.message || 'Unable to update payment mode.');
    }
}

async function updateAdminOrderStatus(orderId, newStatus) {
    let progress = newStatus === 'Completed' ? 100 : (newStatus === 'Cancelled' ? 0 : undefined);
    const body = { status: newStatus };
    if (progress !== undefined) body.progress_percent = progress;
    try {
        const res = await fetch(`/api/admin/orders/${orderId}`, {
            method: 'PUT',
            headers: {'Content-Type': 'application/json'},
            credentials: 'same-origin',
            body: JSON.stringify(body)
        });
        const data = await res.json();
        if (!res.ok || data.status !== 'success') throw new Error(data.message || 'Unable to update order.');
        loadAdminOrders();
        if (staffOrderId === orderId) await openStaffOrderWorkspace(orderId);
    } catch (err) {
        alert(err.message || 'Unable to update order.');
    }
}

async function advanceOrderProgress(orderId, currentProgress) {
    let newProgress = Math.min(100, Number(currentProgress || 0) + 15);
    let newStatus = newProgress === 100 ? 'Completed' : 'In Progress';
    try {
        const res = await fetch(`/api/admin/orders/${orderId}`, {
            method: 'PUT',
            headers: {'Content-Type': 'application/json'},
            credentials: 'same-origin',
            body: JSON.stringify({ progress_percent: newProgress, status: newStatus })
        });
        const data = await res.json();
        if (!res.ok || data.status !== 'success') throw new Error(data.message || 'Unable to update progress.');
        loadAdminOrders();
        if (staffOrderId === orderId) await openStaffOrderWorkspace(orderId);
    } catch (err) {
        alert(err.message || 'Unable to update progress.');
    }
}

async function advanceStaffOrderProgress() {
    if (!staffOrderId) return;
    await advanceOrderProgress(staffOrderId, staffOrderProgress);
}

async function saveStaffOrderNotes() {
    if (!staffOrderId || !canOperateOrderDocuments()) return;
    const notes = document.getElementById('staff-order-notes')?.value || '';
    try {
        const res = await fetch(`/api/admin/orders/${staffOrderId}`, {
            method: 'PUT',
            headers: {'Content-Type': 'application/json'},
            credentials: 'same-origin',
            body: JSON.stringify({ notes })
        });
        const data = await res.json();
        if (!res.ok || data.status !== 'success') throw new Error(data.message || 'Unable to save notes.');
        setStaffOrderNotice('success', 'Internal notes saved.');
        await openStaffOrderWorkspace(staffOrderId);
    } catch (err) {
        setStaffOrderNotice('error', err.message || 'Unable to save notes.');
    }
}

let staffOrderId = null;
let staffOrderClientId = null;
let staffOrderCompanyId = null;
let staffOrderProgress = 0;
let staffOrderRecord = null;
let staffOrderInvoice = null;
let staffCheckoutFormEditing = false;
let staffCheckoutFormDraft = [];

function canOperateOrderDocuments() {
    return currentUser && ['SUPER_ADMIN', 'ADMIN', 'MANAGER', 'STAFF'].includes(currentUser.role);
}

function canManageOrders() {
    return canOperateOrderDocuments();
}

function canDeleteOrders() {
    return currentUser && ['SUPER_ADMIN', 'ADMIN'].includes(currentUser.role);
}

function canEditOrderPrice() {
    return currentUser && ['SUPER_ADMIN', 'ADMIN'].includes(currentUser.role);
}

function canDeleteCompanies() {
    return currentUser && ['SUPER_ADMIN', 'ADMIN'].includes(currentUser.role);
}

function companyNameFromCaches(companyId) {
    const id = Number(companyId);
    const fromDetail = portfolioDetailCache && portfolioDetailCache.company && Number(portfolioDetailCache.company.id) === id
        ? portfolioDetailCache.company.name
        : '';
    if (fromDetail) return fromDetail;
    const fromAdmin = (adminCompaniesCache || []).find((item) => Number(item.id) === id);
    if (fromAdmin && fromAdmin.name) return fromAdmin.name;
    const fromClient = (clientCompaniesCache || []).find((item) => Number(item.id) === id);
    if (fromClient && fromClient.name) return fromClient.name;
    return 'this company';
}

async function deleteCompanyFromPortfolio(companyId) {
    if (!canDeleteCompanies()) {
        alert('You do not have permission to delete companies.');
        return;
    }
    const id = Number(companyId);
    if (!id) return;
    const name = companyNameFromCaches(id);
    if (!confirm(`Delete ${name}? Linked orders keep their data, but this company card will be removed.`)) return;
    try {
        const res = await fetch(`/api/admin/companies/${id}`, {
            method: 'DELETE',
            credentials: 'same-origin',
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data.status !== 'success') {
            throw new Error(data.message || 'Unable to delete company.');
        }
        closeCompanyPortfolioDetail();
        document.querySelectorAll(`[data-company-id="${id}"]`).forEach((el) => el.remove());
        adminCompaniesCache = (adminCompaniesCache || []).filter((item) => Number(item.id) !== id);
        clientCompaniesCache = (clientCompaniesCache || []).filter((item) => Number(item.id) !== id);
        const countPill = document.getElementById('admin-portfolio-company-count');
        if (countPill) countPill.textContent = `${adminCompaniesCache.length} UK`;
        if (typeof loadAdminCompanies === 'function') await loadAdminCompanies();
        else if (typeof loadAdminPortfolioCompanies === 'function') await loadAdminPortfolioCompanies();
    } catch (err) {
        alert(err.message || 'Unable to delete company.');
    }
}

function deleteCompanyFromDetail() {
    const btn = document.getElementById('portfolio-delete-company-btn');
    const companyId = Number(btn && btn.getAttribute('data-company-id'));
    if (companyId) deleteCompanyFromPortfolio(companyId);
}

async function dismissPendingCompany(orderId) {
    if (!canDeleteCompanies()) {
        alert('You do not have permission to remove awaiting-name cards.');
        return;
    }
    const id = Number(orderId);
    if (!id) return;
    if (!confirm('Remove this awaiting-name card from Company Registered? The order stays in Orders Manager.')) return;
    try {
        const res = await fetch(`/api/admin/companies/pending/${id}`, {
            method: 'DELETE',
            credentials: 'same-origin',
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data.status !== 'success') {
            throw new Error(data.message || 'Unable to remove card.');
        }
        document.querySelectorAll(`[data-pending-order-id="${id}"]`).forEach((el) => el.remove());
        if (typeof loadAdminCompanies === 'function') await loadAdminCompanies();
    } catch (err) {
        alert(err.message || 'Unable to remove card.');
    }
}

function canAdvanceOrderProgress(order) {
    const status = String(order && order.status ? order.status : '');
    const pct = Number(order && order.progress_percent ? order.progress_percent : 0);
    if (['Completed', 'Cancelled', 'Refunded'].includes(status)) return false;
    return pct < 100;
}

let pendingDeleteOrderId = null;
let pendingDeleteOrderNumber = '';

function openDeleteOrderModal(orderId, orderNumber) {
    if (!canDeleteOrders() || !orderId) return;
    pendingDeleteOrderId = orderId;
    pendingDeleteOrderNumber = (orderNumber || document.getElementById('staff-order-number')?.textContent || '').trim();
    const numEl = document.getElementById('delete-order-number');
    if (numEl) numEl.textContent = pendingDeleteOrderNumber;
    const input = document.getElementById('delete-order-confirm');
    if (input) input.value = '';
    const err = document.getElementById('delete-order-error');
    if (err) {
        err.style.display = 'none';
        err.textContent = '';
    }
    const modal = document.getElementById('modal-delete-order');
    if (modal) modal.classList.add('active');
    safeCreateIcons();
}

function closeDeleteOrderModal() {
    const modal = document.getElementById('modal-delete-order');
    if (modal) modal.classList.remove('active');
    pendingDeleteOrderId = null;
    pendingDeleteOrderNumber = '';
}

async function confirmDeleteOrder() {
    if (!canDeleteOrders() || !pendingDeleteOrderId) return;
    const typed = (document.getElementById('delete-order-confirm')?.value || '').trim();
    const expected = (pendingDeleteOrderNumber || '').trim();
    const err = document.getElementById('delete-order-error');
    if (expected && typed !== expected) {
        if (err) {
            err.style.display = 'block';
            err.textContent = 'Order number does not match.';
        }
        return;
    }
    try {
        const res = await fetch(`/api/admin/orders/${pendingDeleteOrderId}`, {
            method: 'DELETE',
            credentials: 'same-origin'
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data.status !== 'success') {
            throw new Error(data.message || 'Unable to delete this order.');
        }
        const deletedId = pendingDeleteOrderId;
        closeDeleteOrderModal();
        if (staffOrderId === deletedId) {
            closeStaffOrderModal();
            staffOrderId = null;
        }
        if (typeof loadAdminOrders === 'function') loadAdminOrders();
        if (typeof loadAdminCompanies === 'function') loadAdminCompanies();
    } catch (ex) {
        if (err) {
            err.style.display = 'block';
            err.textContent = ex.message || 'Unable to delete this order.';
        }
    }
}

async function updateStaffOrderAssignee(staffId) {
    if (!staffOrderId || !canManageOrders()) return;
    try {
        const res = await fetch(`/api/admin/orders/${staffOrderId}`, {
            method: 'PUT',
            headers: {'Content-Type': 'application/json'},
            credentials: 'same-origin',
            body: JSON.stringify({ assigned_staff_id: staffId || null })
        });
        const data = await res.json();
        if (!res.ok || data.status !== 'success') throw new Error(data.message || 'Unable to assign staff.');
        setStaffOrderNotice('success', 'Assigned staff updated.');
        await openStaffOrderWorkspace(staffOrderId);
        if (typeof loadAdminOrders === 'function') loadAdminOrders();
    } catch (err) {
        setStaffOrderNotice('error', err.message || 'Unable to assign staff.');
    }
}

function canUploadClientDocuments() {
    return canOperateOrderDocuments();
}

function documentStatusClass(status) {
    if (status === 'Approved') return 'completed';
    if (status === 'Rejected') return 'cancelled';
    return 'pending';
}

function setStaffOrderNotice(kind, message) {
    const err = document.getElementById('staff-order-error');
    const ok = document.getElementById('staff-order-success');
    if (err) {
        err.style.display = kind === 'error' && message ? 'block' : 'none';
        err.textContent = kind === 'error' ? (message || '') : '';
    }
    if (ok) {
        ok.style.display = kind === 'success' && message ? 'block' : 'none';
        ok.textContent = kind === 'success' ? (message || '') : '';
    }
}

function staffOrderPaymentModeOptions(selected) {
    const modes = [
        { value: '', label: 'Select' },
        { value: 'PKR(Bank Transfer)', label: 'PKR' },
        { value: 'GBP(Bank Transfer)', label: 'GBP' },
        { value: 'Website Charge', label: 'Website' },
    ];
    return modes.map((mode) => `<option value="${mode.value}"${mode.value === (selected || '') ? ' selected' : ''}>${mode.label}</option>`).join('');
}

function staffOrderStatusOptions(selected) {
    return ['Pending Verification', 'Pending', 'Processing', 'In Progress', 'Completed', 'Cancelled', 'Refunded'].map((st) =>
        `<option value="${st}"${st === selected ? ' selected' : ''}>${st}</option>`
    ).join('');
}

function collapseCheckoutAddressFields(fields) {
    const rank = {
        street: 0, 'line 1': 0, 'address line 1': 0,
        line2: 1, 'line 2': 1, 'address line 2': 1,
        city: 2, state: 3, county: 3, 'county / state': 3,
        zip: 4, postcode: 4, postal: 4, country: 5
    };
    const groupNames = {
        'registered address': 'Registered address',
        'registered company address': 'Registered address',
        'billing address': 'Billing address',
        'shipping address': 'Shipping address',
        'director address': 'Director address',
        'director home address': 'Director address',
        address: 'Address'
    };
    const grouped = {};
    const result = [];
    (fields || []).forEach((field) => {
        const label = String(field.label || '').trim();
        const value = String(field.value || '').trim();
        const low = label.toLowerCase().replace(/\s+/g, ' ');
        const match = low.match(/^(registered(?: company)? address|billing address|shipping address|director(?: home)? address|address)\s+(.+)$/);
        if (!match || !value) {
            result.push(field);
            return;
        }
        const groupKey = match[1].replace('registered company address', 'registered address').replace('director home address', 'director address');
        let part = match[2].trim().replace('county/state', 'county / state');
        if (rank[part] == null) {
            const last = part.split(' ').pop();
            part = rank[last] == null ? part : last;
        }
        if (rank[part] == null) {
            result.push(field);
            return;
        }
        if (!grouped[groupKey]) {
            grouped[groupKey] = { pos: result.length, parts: {} };
            result.push(null);
        }
        const r = rank[part];
        if (grouped[groupKey].parts[r] == null) grouped[groupKey].parts[r] = value;
    });
    Object.keys(grouped).forEach((key) => {
        const info = grouped[key];
        const line = Object.keys(info.parts).sort((a, b) => Number(a) - Number(b)).map((k) => info.parts[k]).filter(Boolean).join(', ');
        result[info.pos] = { label: groupNames[key] || 'Registered address', value: line };
    });
    return result.filter(Boolean);
}

function checkoutFieldKey(label) {
    const raw = String(label || '').trim().toLowerCase().replace(/[^a-z0-9]+/g, ' ').replace(/\s+/g, ' ').trim();
    const aliases = {
        'passport cnic': 'passport cnic',
        'passport number': 'passport cnic',
        'passport no': 'passport cnic',
        passport: 'passport cnic',
        cnic: 'passport cnic',
        'id number': 'passport cnic',
        'registered address form': 'registered address',
        'registered company address': 'registered address'
    };
    return aliases[raw] || raw;
}

function collapseCheckoutFormFields(fields) {
    const collapsed = collapseCheckoutAddressFields(fields);
    const seen = new Set();
    return collapsed.filter((field) => {
        const key = checkoutFieldKey(field.label);
        if (!key || seen.has(key)) return false;
        seen.add(key);
        return true;
    });
}

function isWideCheckoutField(label) {
    return /address|description|activity|source of funds/i.test(label || '');
}

function setStaffCheckoutFormMode(editing) {
    staffCheckoutFormEditing = Boolean(editing);
    const editBtn = document.getElementById('staff-order-edit-form-btn');
    const addBtn = document.getElementById('staff-order-add-form-btn');
    const saveBtn = document.getElementById('staff-order-save-form-btn');
    const cancelBtn = document.getElementById('staff-order-cancel-form-btn');
    const canEdit = canManageOrders();
    if (editBtn) editBtn.hidden = !canEdit || staffCheckoutFormEditing;
    if (addBtn) addBtn.hidden = !canEdit || !staffCheckoutFormEditing;
    if (saveBtn) saveBtn.hidden = !canEdit || !staffCheckoutFormEditing;
    if (cancelBtn) cancelBtn.hidden = !canEdit || !staffCheckoutFormEditing;
}

function collectStaffCheckoutFormDraft() {
    const checkoutEl = document.getElementById('staff-order-checkout-form');
    if (!checkoutEl) return staffCheckoutFormDraft.slice();
    return Array.from(checkoutEl.querySelectorAll('[data-checkout-field]')).map((row) => ({
        label: (row.querySelector('[data-checkout-label]') || {}).value || row.getAttribute('data-checkout-label') || '',
        value: (row.querySelector('[data-checkout-value]') || {}).value || '',
    })).filter((field) => String(field.label || '').trim());
}

function renderStaffCheckoutForm(fields) {
    const checkoutEl = document.getElementById('staff-order-checkout-form');
    if (!checkoutEl) return;
    const checkoutFields = collapseCheckoutFormFields(fields || []);
    staffCheckoutFormDraft = checkoutFields.map((field) => ({
        label: field.label || '',
        value: field.value || '',
    }));
    setStaffCheckoutFormMode(staffCheckoutFormEditing);
    if (!checkoutFields.length && !staffCheckoutFormEditing) {
        checkoutEl.innerHTML = '<p class="staff-order-section-copy full">No checkout form data stored for this order yet.</p>';
        return;
    }
    if (staffCheckoutFormEditing && canManageOrders()) {
        const rows = (checkoutFields.length ? checkoutFields : [{ label: 'Email (form)', value: '' }]).map((field, index) => {
            const wide = isWideCheckoutField(field.label);
            const control = wide
                ? `<textarea class="select-filter staff-order-form-textarea" data-checkout-value rows="3">${escapeHtml(field.value || '')}</textarea>`
                : `<input type="text" class="select-filter staff-order-form-input" data-checkout-value value="${escapeHtml(field.value || '')}">`;
            return `<div class="${wide ? 'full' : ''}" data-checkout-field data-checkout-label="${escapeHtml(field.label || '')}">
                <label class="staff-order-field-label" for="staff-checkout-field-${index}">${escapeHtml(field.label || '')}</label>
                ${control.replace('data-checkout-value', `id="staff-checkout-field-${index}" data-checkout-value`)}
            </div>`;
        }).join('');
        checkoutEl.innerHTML = rows;
        return;
    }
    checkoutEl.innerHTML = checkoutFields.map((field) => {
        const wide = isWideCheckoutField(field.label);
        return `<div class="${wide ? 'full' : ''}"><span class="staff-order-field-label">${escapeHtml(field.label || '')}</span><strong>${escapeHtml(field.value || '—')}</strong></div>`;
    }).join('');
}

function staffCheckoutFieldsForEdit(order) {
    const existing = collapseCheckoutFormFields((order && order.checkout_form) || staffCheckoutFormDraft || []);
    if (existing.length) return existing;
    return [
        { label: 'Desired company name', value: (order && order.company_name) || '' },
        { label: 'Director name', value: (order && order.company_owner && order.company_owner.full_name) || '' },
        { label: 'Email (form)', value: (order && order.company_owner && order.company_owner.form_email) || '' },
        { label: 'UK contact number', value: (order && order.company_owner && order.company_owner.phone) || (order && order.checkout_phone) || '' },
        { label: 'Package', value: (order && (order.products_summary || order.service_name)) || '' },
        { label: 'Registered address', value: '' },
        { label: 'Director address', value: (order && order.company_owner && order.company_owner.address) || '' },
    ];
}

function beginStaffCheckoutFormEdit() {
    if (!canManageOrders()) return;
    staffCheckoutFormEditing = true;
    const draft = collectStaffCheckoutFormDraft();
    renderStaffCheckoutForm(staffCheckoutFieldsForEdit(Object.assign({}, staffOrderRecord || {}, {
        checkout_form: draft.length ? draft : ((staffOrderRecord && staffOrderRecord.checkout_form) || [])
    })));
}

function addStaffCheckoutFormField() {
    if (!canManageOrders() || !staffCheckoutFormEditing) return;
    const label = window.prompt('Field name to add (for example Email, SIC code, or Package)');
    if (!label || !String(label).trim()) return;
    const draft = collectStaffCheckoutFormDraft();
    draft.push({ label: String(label).trim(), value: '' });
    renderStaffCheckoutForm(draft);
}

function cancelStaffCheckoutFormEdit() {
    staffCheckoutFormEditing = false;
    if (staffOrderId) openStaffOrderWorkspace(staffOrderId);
}

async function saveStaffCheckoutFormEdit() {
    if (!canManageOrders() || !staffOrderId) return;
    const checkout_form = collectStaffCheckoutFormDraft();
    try {
        const res = await fetch(`/api/admin/orders/${staffOrderId}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'same-origin',
            body: JSON.stringify({ checkout_form }),
        });
        const data = await res.json();
        if (!res.ok || data.status !== 'success') throw new Error(data.message || 'Unable to save checkout details.');
        staffCheckoutFormEditing = false;
        await openStaffOrderWorkspace(staffOrderId);
        setStaffOrderNotice('success', 'Checkout details updated.');
        if (typeof loadAdminOrders === 'function') loadAdminOrders();
        if (typeof loadAdminCompanies === 'function') loadAdminCompanies();
    } catch (err) {
        setStaffOrderNotice('error', err.message || 'Unable to save checkout details.');
    }
}

async function openStaffOrderWorkspace(orderId, options) {
    if (!currentUser || currentUser.role === 'CLIENT') {
        alert('Staff sign-in is required to process an order.');
        return;
    }
    const editCheckout = Boolean(options && options.editCheckout) && canManageOrders();
    if (Number(staffOrderId) !== Number(orderId)) staffCheckoutFormEditing = false;
    if (editCheckout) staffCheckoutFormEditing = true;
    setStaffOrderNotice('', '');
    try {
        const orderRes = await fetch(`/api/client/orders/${orderId}`, { credentials: 'same-origin' });
        const data = await orderRes.json();
        if (!orderRes.ok || data.status !== 'success') {
            throw new Error(data.message || 'Unable to load order.');
        }
        const order = data.order;
        staffOrderId = order.id;
        staffOrderClientId = order.user_id;
        staffOrderCompanyId = order.company_id;
        staffOrderProgress = Number(order.progress_percent) || 0;
        staffOrderRecord = order;
        document.getElementById('staff-order-number').textContent = order.order_number;
        const createdLabel = formatDateTime(order.created_at) || formatDate(order.created_at);
        const showFinance = canViewRevenue();
        document.getElementById('staff-order-meta').textContent = showFinance
            ? `${createdLabel} · £${parseFloat(order.total || 0).toFixed(2)}`
            : createdLabel;
        setPortfolioText('staff-order-date', formatShortDate(order.created_at) || formatDate(order.created_at));
        setPortfolioText('staff-order-company', order.company_name || '—');
        const owner = order.company_owner || {};
        setPortfolioText('staff-order-owner-name', owner.full_name || '—');
        setPortfolioText('staff-order-owner-email', owner.form_email || '—');
        setPortfolioText('staff-order-owner-phone', owner.phone || '—');
        setPortfolioText('staff-order-portal-email', order.portal_login_email || order.client_email || '—');

        const dispEmail = document.getElementById('disp-access-email');
        const dispPass = document.getElementById('disp-access-password');
        const btnCopyPass = document.getElementById('btn-copy-pass');
        const accEmail = order.access_email || '';
        const accPass = order.access_email_password || '';
        if (dispEmail) dispEmail.textContent = accEmail || 'None recorded';
        if (dispPass) {
            dispPass.setAttribute('data-actual-password', accPass);
            dispPass.textContent = '••••••••';
        }
        if (btnCopyPass) btnCopyPass.setAttribute('data-pass', accPass);
        if (typeof toggleStaffOrderCredentialsEdit === 'function') {
            toggleStaffOrderCredentialsEdit(false);
        }

        const productSummary = order.products_summary || order.service_name || '—';
        const productEl = document.getElementById('staff-order-product');
        if (productEl) productEl.innerHTML = renderProductPills(productSummary, order.category_name);
        const priceBits = showFinance ? [`£${parseFloat(order.total || 0).toFixed(2)}`] : [];
        if (showFinance && order.price != null && order.vat != null) {
            priceBits.push(`(£${parseFloat(order.price || 0).toFixed(2)} + £${parseFloat(order.vat || 0).toFixed(2)} VAT)`);
        }
        setPortfolioText('staff-order-price', priceBits.join(' ') || '—');
        const priceEl = document.getElementById('staff-order-price');
        if (priceEl) priceEl.dataset.total = parseFloat(order.total || 0).toFixed(2);
        const priceEditBtn = document.getElementById('staff-order-price-edit');
        const priceEditor = document.getElementById('staff-order-price-editor');
        const priceInput = document.getElementById('staff-order-price-input');
        const priceSave = document.getElementById('staff-order-price-save');
        if (priceEditor) priceEditor.hidden = true;
        if (priceEditBtn) {
            priceEditBtn.hidden = !canEditOrderPrice();
            priceEditBtn.onclick = () => beginStaffOrderPriceEdit();
        }
        if (priceInput) {
            priceInput.value = parseFloat(order.total || 0).toFixed(2);
            priceInput.onkeydown = (e) => {
                if (e.key === 'Enter') {
                    e.preventDefault();
                    saveStaffOrderPriceEdit();
                }
                if (e.key === 'Escape') {
                    e.preventDefault();
                    if (priceEditor) priceEditor.hidden = true;
                    if (priceEditBtn && canEditOrderPrice()) priceEditBtn.hidden = false;
                }
            };
        }
        if (priceSave) priceSave.onclick = () => saveStaffOrderPriceEdit();
        const paymentSel = document.getElementById('staff-order-payment-mode');
        if (paymentSel) {
            paymentSel.innerHTML = staffOrderPaymentModeOptions(order.payment_mode || '');
            paymentSel.disabled = !canManageOrders();
        }
        const notesEl = document.getElementById('staff-order-notes');
        if (notesEl) notesEl.value = order.notes || '';
        const statusSel = document.getElementById('staff-order-status-select');
        if (statusSel) {
            statusSel.innerHTML = staffOrderStatusOptions(order.status);
            statusSel.disabled = !canManageOrders();
        }
        const manage = document.getElementById('staff-order-manage');
        if (manage) manage.style.display = (canManageOrders() || canDeleteOrders()) ? 'flex' : 'none';
        const deleteBtn = document.getElementById('staff-order-delete-btn');
        if (deleteBtn) deleteBtn.style.display = canDeleteOrders() ? 'inline-flex' : 'none';
        const progressBtn = document.getElementById('staff-order-progress-btn');
        if (progressBtn) progressBtn.style.display = canAdvanceOrderProgress(order) ? 'inline-flex' : 'none';
        fillStaffOrderPaymentPlan(data.invoice, order);
        const checkoutFields = staffCheckoutFormEditing
            ? staffCheckoutFieldsForEdit(order)
            : collapseCheckoutFormFields(order.checkout_form || []);
        renderStaffCheckoutForm(checkoutFields);
        const lines = data.line_items || [];
        const productHead = showFinance
            ? '<th>Category</th><th>Product</th><th>SKU</th><th>Qty</th><th>Unit</th><th>Total</th>'
            : '<th>Category</th><th>Product</th><th>SKU</th><th>Qty</th>';
        document.getElementById('staff-order-products').innerHTML = lines.length ? `<table class="data-table"><thead><tr>${productHead}</tr></thead><tbody>${lines.map((item) => `
            <tr>
                <td>${escapeHtml(item.category_name || '—')}</td>
                <td>${escapeHtml(item.product_name)}</td>
                <td>${escapeHtml(item.sku || '—')}</td>
                <td>${item.quantity}</td>
                ${showFinance ? `<td>£${parseFloat(item.unit_price || 0).toFixed(2)}</td><td>£${parseFloat(item.line_total || 0).toFixed(2)}</td>` : ''}
            </tr>`).join('')}</tbody></table>` : `<p class="staff-order-section-copy">${escapeHtml(productSummary)}${showFinance ? ` · £${parseFloat(order.price || order.total || 0).toFixed(2)}` : ''}</p>`;
        const docs = (data.documents || []).filter((d) => d.is_customer_upload || d.uploaded_by === 'Customer Upload' || d.category === 'Checkout Upload');
        document.getElementById('staff-order-documents').innerHTML = docs.length ? docs.map((d) => `
            <div class="staff-order-doc-row">
                <div>
                    <strong>${escapeHtml(d.name)}</strong>
                    <div class="staff-order-field-sub">Customer checkout upload · ${escapeHtml(d.status || 'Pending Review')} · ${escapeHtml(d.file_type || '')}</div>
                </div>
                <div class="doc-file-actions">
                    ${documentActionButtons(d)}
                </div>
            </div>`).join('') : '';
        const modal = document.getElementById('modal-staff-order');
        if (modal) {
            modal.classList.toggle('hide-order-finance', !showFinance);
            modal.classList.add('active');
        }
        if (editCheckout) {
            const checkoutEl = document.getElementById('staff-order-checkout-form');
            if (checkoutEl) checkoutEl.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
        }
        safeCreateIcons();
    } catch (err) {
        alert(err.message || 'Unable to open order.');
    }
}

function closeStaffOrderModal() {
    staffCheckoutFormEditing = false;
    const modal = document.getElementById('modal-staff-order');
    if (modal) modal.classList.remove('active');
}

async function uploadStaffOrderDocument() {
    openDeliverDocumentModalFromOrder();
}

async function sendOrderDocumentToCustomer(docId) {
    if (!canOperateOrderDocuments()) return;
    if (!confirm('Send this document to the customer portal?')) return;
    try {
        const res = await fetch(`/api/admin/documents/${docId}/send`, {
            method: 'POST',
            credentials: 'same-origin'
        });
        const data = await res.json();
        if (!res.ok || data.status !== 'success') throw new Error(data.message || 'Unable to send document.');
        setStaffOrderNotice('success', data.message);
        if (staffOrderId) await openStaffOrderWorkspace(staffOrderId);
    } catch (err) {
        setStaffOrderNotice('error', err.message || 'Unable to send document.');
    }
}

async function sendStaffOrderMessage() {
    if (!staffOrderId || !canOperateOrderDocuments()) return;
    const box = document.getElementById('staff-order-message');
    const message = box ? box.value.trim() : '';
    if (!message) {
        setStaffOrderNotice('error', 'Enter a message.');
        return;
    }
    try {
        const res = await fetch(`/api/admin/orders/${staffOrderId}/conversation`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            credentials: 'same-origin',
            body: JSON.stringify({ message })
        });
        const data = await res.json();
        if (!res.ok || data.status !== 'success') throw new Error(data.message || 'Unable to send message.');
        if (box) box.value = '';
        setStaffOrderNotice('success', 'Message sent to the customer.');
        await openStaffOrderWorkspace(staffOrderId);
    } catch (err) {
        setStaffOrderNotice('error', err.message || 'Unable to send message.');
    }
}

async function createTaskFromStaffOrder() {
    if (!canAssignStaffTasks() || !staffOrderId) return;
    closeStaffOrderModal();
    await openNewTaskModal(null, staffOrderClientId, staffOrderId);
}

let taskStaffCache = [];
let taskCustomerCache = [];
let taskAssocCache = {};

function canAssignStaffTasks() {
    return currentUser && ['ADMIN', 'SUPER_ADMIN', 'MANAGER'].includes(currentUser.role);
}

function showTasksError(message) {
    const box = document.getElementById('adm-tasks-error');
    if (!box) return;
    if (!message) {
        box.style.display = 'none';
        box.textContent = '';
        return;
    }
    box.textContent = message;
    box.style.display = 'block';
}

function showModalError(id, message) {
    const box = document.getElementById(id);
    if (!box) return;
    if (!message) {
        box.style.display = 'none';
        box.textContent = '';
        return;
    }
    box.textContent = message;
    box.style.display = 'block';
}

const PRODUCT_BANKS = [
    { re: /\btide\b/, tone: 'tide' },
    { re: /\btap\s*tap\b|\btaptap\b/, tone: 'taptap' },
    { re: /\bwise\b/, tone: 'wise' },
    { re: /\bmonzo\b/, tone: 'monzo' },
    { re: /\bzempler\b/, tone: 'zempler' },
    { re: /\bifast\b/, tone: 'ifast' },
    { re: /\bcounting\s*up\b|\bcountingup\b/, tone: 'countingup' },
    { re: /\btranswap\b/, tone: 'transwap' },
    { re: /\brevolut\b/, tone: 'bank' },
    { re: /\bstarling\b/, tone: 'bank' },
];

function matchProductBanks(name) {
    const n = String(name || '').toLowerCase();
    return PRODUCT_BANKS.filter((bank) => bank.re.test(n));
}

function productVisual(name) {
    const n = String(name || '').toLowerCase();
    const banks = matchProductBanks(name);
    if (banks.length) return { tone: banks[0].tone };
    if (/identity|kyc|verification/.test(n)) return { tone: 'kyc' };
    if (/all inclusive|all-inclusive/.test(n)) return { tone: 'inclusive' };
    if (/professional/.test(n)) return { tone: 'pro' };
    if (/digital/.test(n)) return { tone: 'digital' };
    if (/\bbank\b/.test(n)) return { tone: 'bank' };
    if (/vat|tax|hmrc/.test(n)) return { tone: 'tax' };
    if (/registered office|office address/.test(n)) return { tone: 'address' };
    if (/mail/.test(n)) return { tone: 'mail' };
    if (/website|web design|marketing/.test(n)) return { tone: 'web' };
    if (/confirmation statement/.test(n)) return { tone: 'forms' };
    if (/dissolution/.test(n)) return { tone: 'forms' };
    if (/name change/.test(n)) return { tone: 'forms' };
    if (/dormant/.test(n)) return { tone: 'forms' };
    if (/director/.test(n)) return { tone: 'forms' };
    if (/formation|incorporat|company registration/.test(n)) return { tone: 'digital' };
    if (/forms/.test(n)) return { tone: 'forms' };
    return { tone: 'default' };
}

function renderProductPills(summary, categoryName) {
    const parts = String(summary || '')
        .split(',')
        .map((part) => part.trim())
        .filter(Boolean);
    if (!parts.length) return '—';
    return `<div class="product-pills">${parts.map((name) => {
        const visual = productVisual(`${categoryName || ''} ${name}`);
        return `<span class="product-pill product-pill-${visual.tone}" title="${escapeHtml(name)}"><span class="product-pill-text">${escapeHtml(name)}</span></span>`;
    }).join('')}</div>`;
}

function escapeHtml(value) {
    return String(value == null ? '' : value)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

function formatDocumentSize(numBytes) {
    const n = Number(numBytes) || 0;
    if (n >= 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MB`;
    if (n >= 1024) return `${(n / 1024).toFixed(1)} KB`;
    return `${n} B`;
}

function optionalId(value) {
    if (value === undefined || value === null || value === '') return null;
    const parsed = parseInt(value, 10);
    return Number.isNaN(parsed) ? null : parsed;
}

function taskStatusClass(status) {
    const key = (status || '').toLowerCase().replace(/ /g, '-');
    if (key === 'completed') return 'completed';
    if (key === 'in-progress') return 'in-progress';
    if (key === 'cancelled') return 'cancelled';
    return 'open';
}

function taskPriorityClass(priority) {
    if (priority === 'Urgent') return 'cancelled';
    if (priority === 'High') return 'pending';
    if (priority === 'Low') return 'open';
    return 'in-progress';
}

function applyTaskUiPermissions() {
    const canAssign = canAssignStaffTasks();
    syncTasksTeamChrome();
    const assigneeFilter = document.getElementById('filter-task-assignee');
    if (assigneeFilter) assigneeFilter.style.display = canAssign ? '' : 'none';
}

let activeTasksTeamTab = 'tasks';

function syncTasksTeamChrome() {
    const teamTab = document.getElementById('tab-tasks-team-team');
    if (teamTab) teamTab.style.display = canManageUsers() ? '' : 'none';
    const createBtn = document.getElementById('btn-create-user');
    const onTeam = activeView === 'admin-tasks' && activeTasksTeamTab === 'team';
    if (createBtn && activeView === 'admin-tasks') {
        createBtn.style.display = (onTeam && canManageUsers()) ? 'inline-flex' : 'none';
    }
}

function switchTasksTeamTab(tabName) {
    const tab = (tabName === 'team' && canManageUsers()) ? 'team' : 'tasks';
    activeTasksTeamTab = tab;
    document.querySelectorAll('.tasks-team-tabs .portfolio-tab').forEach((btn) => {
        btn.classList.toggle('active', btn.getAttribute('data-tab') === tab);
    });
    document.querySelectorAll('#view-admin-tasks .tasks-team-panel').forEach((panel) => {
        panel.hidden = panel.getAttribute('data-panel') !== tab;
    });
    syncTasksTeamChrome();
    if (tab === 'team') loadAdminTeam();
    else loadQuickTasks();
    if (window.lucide) lucide.createIcons();
}

const QUICK_TASK_TEMPLATES = {
    'uk-formfill-ad01': {
        group: 'UK Formfill',
        formCode: 'AD01',
        title: 'UK Formfill — AD01 Form',
        // Opens UK FormFill Pro (change of registered office address), not a CRM task form.
        openFormfill: true,
    },
};

const DEFAULT_UK_FORMFILL_URL = 'https://portal.brixenconsultants.com/formfill';
let ukFormfillUrlCache = '';

async function resolveUkFormfillUrl() {
    if (ukFormfillUrlCache) return ukFormfillUrlCache;
    try {
        const res = await fetch('/api/admin/formfill', { credentials: 'same-origin' });
        const data = await res.json().catch(() => ({}));
        if (res.ok && data.status === 'success' && data.url) {
            ukFormfillUrlCache = String(data.url).replace(/\/$/, '');
            return ukFormfillUrlCache;
        }
    } catch (err) {
        /* fall through to default */
    }
    ukFormfillUrlCache = DEFAULT_UK_FORMFILL_URL;
    return ukFormfillUrlCache;
}

async function loadQuickTasks() {
    if (window.lucide) lucide.createIcons();
}

async function launchQuickTask(templateId, event) {
    if (event) {
        event.preventDefault();
        event.stopPropagation();
    }
    const template = QUICK_TASK_TEMPLATES[templateId];
    if (!template) {
        alert('Quick task template not found.');
        return;
    }
    if (!isAdminShellUser(currentUser)) {
        alert('Staff only.');
        return;
    }
    if (template.openFormfill) {
        await openAd01FormModal(template.formCode || 'AD01');
        return;
    }
}

async function openAd01FormModal(formCode) {
    const modal = document.getElementById('modal-ad01-form');
    const frame = document.getElementById('ad01-form-frame');
    if (!modal || !frame) return;
    const base = await resolveUkFormfillUrl();
    const form = encodeURIComponent(formCode || 'AD01');
    frame.src = `${base}/?form=${form}&embed=1`;
    modal.classList.add('active');
    if (window.lucide) lucide.createIcons();
}

function closeAd01FormModal() {
    const modal = document.getElementById('modal-ad01-form');
    const frame = document.getElementById('ad01-form-frame');
    if (modal) modal.classList.remove('active');
    if (frame) frame.src = 'about:blank';
}

function onAd01FormModalBackdrop(event) {
    if (event.target && event.target.id === 'modal-ad01-form') {
        closeAd01FormModal();
    }
}

async function fetchTaskStaff() {
    const res = await fetch('/api/admin/staff');
    const data = await res.json();
    if (!res.ok || data.status !== 'success') {
        throw new Error(data.message || 'Unable to load staff list.');
    }
    taskStaffCache = data.staff || [];
    return taskStaffCache;
}

async function fetchTaskCustomers() {
    const res = await fetch('/api/admin/customers', { credentials: 'same-origin' });
    const data = await res.json();
    if (!res.ok || data.status !== 'success') {
        throw new Error(data.message || 'Unable to load clients.');
    }
    taskCustomerCache = data.customers || [];
    return taskCustomerCache;
}

function fillDepartmentSelect(selectId, selectedValue, includeBlankLabel) {
    const sel = document.getElementById(selectId);
    if (!sel) return;
    const blank = includeBlankLabel || 'Select department';
    sel.innerHTML = `<option value="">${escapeHtml(blank)}</option>` + STAFF_DEPARTMENTS.map((dept) =>
        `<option value="${escapeHtml(dept)}">${escapeHtml(dept)}</option>`
    ).join('');
    if (selectedValue) sel.value = selectedValue;
}

function staffDepartments(staff) {
    if (!staff) return [];
    if (Array.isArray(staff.departments) && staff.departments.length) return staff.departments;
    if (staff.department) return [staff.department];
    return [];
}

function staffForDepartment(department) {
    if (!department) return taskStaffCache;
    const matched = taskStaffCache.filter((s) => staffDepartments(s).includes(department));
    const unassigned = taskStaffCache.filter((s) => staffDepartments(s).length === 0);
    const seen = new Set();
    return [...matched, ...unassigned].filter((s) => {
        if (seen.has(s.id)) return false;
        seen.add(s.id);
        return true;
    });
}

function fillStaffSelect(selectId, selectedId, includeAllLabel, department) {
    const sel = document.getElementById(selectId);
    if (!sel) return;
    const first = includeAllLabel || 'Unassigned';
    const staff = staffForDepartment(department);
    sel.innerHTML = `<option value="">${escapeHtml(first)}</option>` + staff.map(s => {
        const dept = staffDepartments(s).length ? ` · ${staffDepartments(s).join(', ')}` : '';
        return `<option value="${s.id}">${escapeHtml(s.full_name)}${escapeHtml(dept)}</option>`;
    }).join('');
    if (selectedId) sel.value = String(selectedId);
}

function onTaskDepartmentChange(prefix) {
    const dept = document.getElementById(`${prefix}-task-department`)?.value || '';
    const current = document.getElementById(`${prefix}-task-assignee`)?.value || '';
    fillStaffSelect(`${prefix}-task-assignee`, current, 'Anyone in this department', dept);
}

function fillClientSelect(selectId, selectedId) {
    const sel = document.getElementById(selectId);
    if (!sel) return;
    sel.innerHTML = `<option value="">No client</option>` + taskCustomerCache.map(c =>
        `<option value="${c.id}">${escapeHtml(c.full_name)}</option>`
    ).join('');
    if (selectedId) sel.value = String(selectedId);
}

async function loadTaskAssociations(clientId) {
    if (!clientId) return { companies: [], orders: [] };
    if (taskAssocCache[clientId]) return taskAssocCache[clientId];
    const res = await fetch(`/api/admin/clients/${clientId}/full`);
    const data = await res.json();
    if (!res.ok || data.status !== 'success') {
        throw new Error(data.message || 'Unable to load client companies and orders.');
    }
    const packed = { companies: data.companies || [], orders: data.orders || [] };
    taskAssocCache[clientId] = packed;
    return packed;
}

function fillCompanyOrderSelects(prefix, assoc, selectedCompanyId, selectedOrderId) {
    const companySel = document.getElementById(`${prefix}-task-company`);
    const orderSel = document.getElementById(`${prefix}-task-order`);
    const companies = assoc.companies || [];
    const orders = assoc.orders || [];
    if (companySel) {
        companySel.innerHTML = `<option value="">No company</option>` + companies.map(c =>
            `<option value="${c.id}">${escapeHtml(c.name)}</option>`
        ).join('');
        if (selectedCompanyId) companySel.value = String(selectedCompanyId);
    }
    fillTaskOrderSelect(prefix, orders, companySel ? companySel.value : '', selectedOrderId);
}

function fillTaskOrderSelect(prefix, orders, companyId, selectedOrderId) {
    const orderSel = document.getElementById(`${prefix}-task-order`);
    if (!orderSel) return;
    const filtered = companyId
        ? orders.filter(o => !o.company_id || String(o.company_id) === String(companyId))
        : orders;
    orderSel.innerHTML = `<option value="">No order</option>` + filtered.map(o =>
        `<option value="${o.id}">${escapeHtml(o.order_number)} — ${escapeHtml(o.service_name || '')}</option>`
    ).join('');
    if (selectedOrderId) orderSel.value = String(selectedOrderId);
}

async function onTaskAssociationChange(prefix, selectedCompanyId, selectedOrderId) {
    const clientId = document.getElementById(`${prefix}-task-client`)?.value;
    try {
        const assoc = await loadTaskAssociations(clientId);
        fillCompanyOrderSelects(prefix, assoc, selectedCompanyId, selectedOrderId);
    } catch (err) {
        showModalError(prefix === 'new' ? 'new-task-error' : 'edit-task-error', err.message);
    }
}

async function onTaskCompanyChange(prefix) {
    const clientId = document.getElementById(`${prefix}-task-client`)?.value;
    const companyId = document.getElementById(`${prefix}-task-company`)?.value;
    try {
        const assoc = await loadTaskAssociations(clientId);
        fillTaskOrderSelect(prefix, assoc.orders || [], companyId, '');
    } catch (err) {
        showModalError(prefix === 'new' ? 'new-task-error' : 'edit-task-error', err.message);
    }
}

function setAssociationFieldsEnabled(enabled) {
    ['edit-task-assignee', 'edit-task-client', 'edit-task-company', 'edit-task-order'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.disabled = !enabled;
    });
}

async function loadAdminTasks() {
    const tbody = document.getElementById('adm-tasks-table-body');
    if (!tbody) return;
    applyTaskUiPermissions();

    try {
        await fetchTaskStaff();
        const deptFilter = document.getElementById('filter-task-department');
        const previousDept = deptFilter ? deptFilter.value : '';
        fillDepartmentSelect('filter-task-department', previousDept, 'All departments');
        const assigneeSel = document.getElementById('filter-task-assignee');
        const previousAssignee = assigneeSel ? assigneeSel.value : '';
        fillStaffSelect('filter-task-assignee', previousAssignee, 'All Assignees', previousDept);
        if (assigneeSel && previousAssignee) assigneeSel.value = previousAssignee;

        const params = new URLSearchParams();
        const status = document.getElementById('filter-task-status')?.value;
        const priority = document.getElementById('filter-task-priority')?.value;
        const department = document.getElementById('filter-task-department')?.value;
        const assignee = document.getElementById('filter-task-assignee')?.value;
        const due = document.getElementById('filter-task-due')?.value;
        if (status) params.set('status', status);
        if (priority) params.set('priority', priority);
        if (department) params.set('department', department);
        if (due) params.set('due', due);
        if (assignee && canAssignStaffTasks()) params.set('assigned_staff_id', assignee);

        const qs = params.toString();
        const res = await fetch(`/api/admin/tasks${qs ? '?' + qs : ''}`);
        const data = await res.json();
        if (!res.ok || data.status !== 'success') {
            throw new Error(data.message || 'Unable to load tasks.');
        }
        renderAdminTasks(data.tasks || []);
    } catch (err) {
        console.error(err);
        showTasksError(err.message || 'Unable to load tasks.');
        if (tbody) {
            tbody.innerHTML = `<tr><td colspan="9" style="text-align:center; color:#dc2626; padding:28px;">Unable to load tasks. Please try again.</td></tr>`;
        }
    }
}

function renderAdminTasks(tasks) {
    const tbody = document.getElementById('adm-tasks-table-body');
    if (!tbody) return;
    // Formfill shortcuts live in Quick Tasks only — hide legacy assigned Formfill rows.
    const list = (tasks || []).filter((t) => !/^UK Formfill\b/i.test(String(t.title || '')));
    if (!list.length) {
        tbody.innerHTML = `<tr><td colspan="9" style="text-align:center; color:#64748b; padding:36px;">No tasks found.</td></tr>`;
        lucide.createIcons();
        return;
    }
    tbody.innerHTML = list.map(t => {
        const canComplete = t.status !== 'Completed' && t.status !== 'Cancelled';
        return `
            <tr>
                <td style="font-weight:700;">${escapeHtml(t.title)}</td>
                <td>${t.department ? `<span class="dept-badge">${escapeHtml(t.department)}</span>` : '—'}</td>
                <td>${escapeHtml(t.assigned_staff_name || (t.department ? 'Department queue' : 'Unassigned'))}</td>
                <td>${escapeHtml(t.client_name || '—')}</td>
                <td style="font-weight:600; color:var(--brand-primary);">${escapeHtml(t.order_number || '—')}</td>
                <td><span class="status-badge ${taskPriorityClass(t.priority)}">${escapeHtml(t.priority)}</span></td>
                <td>${t.due_date ? formatDate(t.due_date) : '—'}</td>
                <td><span class="status-badge ${taskStatusClass(t.status)}">${escapeHtml(t.status)}</span></td>
                <td>
                    <button class="btn-primary" style="padding:4px 10px; font-size:0.75rem; margin-right:6px;" onclick="openEditTaskModal(${t.id})">Open</button>
                    ${canComplete ? `<button class="btn-secondary" style="padding:4px 10px; font-size:0.75rem;" onclick="completeAdminTask(${t.id})">Complete</button>` : ''}
                </td>
            </tr>
        `;
    }).join('');
    lucide.createIcons();
}

async function openNewTaskModal(prefillAssigneeId, prefillClientId, prefillOrderId) {
    if (!canAssignStaffTasks()) {
        alert('You do not have permission to create tasks.');
        return;
    }
    showModalError('new-task-error', '');
    const form = document.getElementById('new-task-form');
    if (form) form.reset();
    document.getElementById('new-task-priority').value = 'Medium';
    try {
        await Promise.all([fetchTaskStaff(), fetchTaskCustomers()]);
        fillDepartmentSelect('new-task-department', '', 'Select department');
        fillStaffSelect('new-task-assignee', prefillAssigneeId || '', 'Anyone in this department', '');
        fillClientSelect('new-task-client', prefillClientId || '');
        if (prefillClientId) {
            await onTaskAssociationChange('new');
            const orderSel = document.getElementById('new-task-order');
            if (orderSel && prefillOrderId) orderSel.value = String(prefillOrderId);
        } else {
            fillCompanyOrderSelects('new', { companies: [], orders: [] }, '', '');
        }
        document.getElementById('modal-new-task').classList.add('active');
        lucide.createIcons();
    } catch (err) {
        alert(err.message || 'Unable to open new task form.');
    }
}

function closeNewTaskModal() {
    const modal = document.getElementById('modal-new-task');
    if (modal) modal.classList.remove('active');
    const heading = document.querySelector('#modal-new-task .modal-header h3');
    if (heading) heading.textContent = 'New Task';
}

function collectTaskForm(prefix, includeStatus) {
    const payload = {
        title: document.getElementById(`${prefix}-task-title`).value.trim(),
        description: document.getElementById(`${prefix}-task-description`).value,
        priority: document.getElementById(`${prefix}-task-priority`).value,
        due_date: document.getElementById(`${prefix}-task-due`).value || null,
        department: document.getElementById(`${prefix}-task-department`)?.value || '',
        assigned_staff_id: optionalId(document.getElementById(`${prefix}-task-assignee`).value),
        client_id: optionalId(document.getElementById(`${prefix}-task-client`).value),
        company_id: optionalId(document.getElementById(`${prefix}-task-company`).value),
        order_id: optionalId(document.getElementById(`${prefix}-task-order`).value),
        internal_notes: document.getElementById(`${prefix}-task-notes`).value
    };
    if (includeStatus) {
        payload.status = document.getElementById('edit-task-status').value;
    }
    return payload;
}

async function submitNewTaskForm(e) {
    e.preventDefault();
    showModalError('new-task-error', '');
    const submitBtn = document.getElementById('new-task-submit');
    const payload = collectTaskForm('new', false);
    if (!payload.title) {
        showModalError('new-task-error', 'Title is required.');
        return;
    }
    if (submitBtn) submitBtn.disabled = true;
    try {
        const res = await fetch('/api/admin/tasks', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(payload)
        });
        const data = await res.json();
        if (!res.ok || data.status !== 'success') {
            throw new Error(data.message || 'Unable to create task.');
        }
        closeNewTaskModal();
        alert('Task created successfully.');
        loadAdminTasks();
        loadNotificationsCount();
    } catch (err) {
        showModalError('new-task-error', err.message || 'Unable to create task.');
    } finally {
        if (submitBtn) submitBtn.disabled = false;
    }
}

async function openEditTaskModal(taskId) {
    showModalError('edit-task-error', '');
    try {
        const [taskRes] = await Promise.all([
            fetch(`/api/admin/tasks/${taskId}`),
            fetchTaskStaff(),
            fetchTaskCustomers()
        ]);
        const data = await taskRes.json();
        if (!taskRes.ok || data.status !== 'success') {
            throw new Error(data.message || 'Unable to load task.');
        }
        const t = data.task;
        document.getElementById('edit-task-id').value = t.id;
        document.getElementById('edit-task-heading').textContent = t.title;
        document.getElementById('edit-task-meta').textContent = t.created_by_name ? `Created by ${t.created_by_name}` : 'Internal staff task';
        document.getElementById('edit-task-title').value = t.title || '';
        document.getElementById('edit-task-description').value = t.description || '';
        document.getElementById('edit-task-priority').value = t.priority || 'Medium';
        document.getElementById('edit-task-status').value = t.status || 'Open';
        document.getElementById('edit-task-due').value = t.due_date ? String(t.due_date).slice(0, 10) : '';
        document.getElementById('edit-task-notes').value = t.internal_notes || '';
        fillDepartmentSelect('edit-task-department', t.department || '', 'Select department');
        fillStaffSelect('edit-task-assignee', t.assigned_staff_id, 'Anyone in this department', t.department || '');
        fillClientSelect('edit-task-client', t.client_id);
        await onTaskAssociationChange('edit', t.company_id, t.order_id);
        setAssociationFieldsEnabled(canAssignStaffTasks());
        const completeBtn = document.getElementById('edit-task-complete-btn');
        if (completeBtn) completeBtn.style.display = (t.status === 'Completed' || t.status === 'Cancelled') ? 'none' : 'inline-flex';
        document.getElementById('modal-edit-task').classList.add('active');
        lucide.createIcons();
    } catch (err) {
        alert(err.message || 'Unable to open task.');
    }
}

function closeEditTaskModal() {
    const modal = document.getElementById('modal-edit-task');
    if (modal) modal.classList.remove('active');
}

async function submitEditTaskForm(e) {
    e.preventDefault();
    showModalError('edit-task-error', '');
    const taskId = document.getElementById('edit-task-id').value;
    const submitBtn = document.getElementById('edit-task-submit');
    const payload = collectTaskForm('edit', true);
    if (!payload.title) {
        showModalError('edit-task-error', 'Title is required.');
        return;
    }
    if (!canAssignStaffTasks()) {
        delete payload.assigned_staff_id;
        delete payload.department;
        delete payload.client_id;
        delete payload.company_id;
        delete payload.order_id;
    }
    if (submitBtn) submitBtn.disabled = true;
    try {
        const res = await fetch(`/api/admin/tasks/${taskId}`, {
            method: 'PUT',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(payload)
        });
        const data = await res.json();
        if (!res.ok || data.status !== 'success') {
            throw new Error(data.message || 'Unable to update task.');
        }
        closeEditTaskModal();
        alert('Task updated successfully.');
        loadAdminTasks();
    } catch (err) {
        showModalError('edit-task-error', err.message || 'Unable to update task.');
    } finally {
        if (submitBtn) submitBtn.disabled = false;
    }
}

async function completeAdminTask(taskId) {
    try {
        const res = await fetch(`/api/admin/tasks/${taskId}/complete`, { method: 'POST' });
        const data = await res.json();
        if (!res.ok || data.status !== 'success') {
            throw new Error(data.message || 'Unable to complete task.');
        }
        alert('Task marked complete.');
        closeEditTaskModal();
        loadAdminTasks();
        loadNotificationsCount();
    } catch (err) {
        alert(err.message || 'Unable to complete task.');
    }
}

async function completeTaskFromModal() {
    const taskId = document.getElementById('edit-task-id').value;
    if (taskId) await completeAdminTask(taskId);
}

function setTeamMessage(kind, message) {
    const errBox = document.getElementById('adm-team-error');
    const okBox = document.getElementById('adm-team-success');
    const flash = document.getElementById('app-flash-notice');
    if (errBox) {
        errBox.style.display = kind === 'error' && message ? 'block' : 'none';
        errBox.textContent = kind === 'error' ? (message || '') : '';
    }
    if (okBox) {
        okBox.style.display = kind === 'success' && message ? 'block' : 'none';
        okBox.textContent = kind === 'success' ? (message || '') : '';
    }
    if (flash) {
        flash.style.display = kind === 'success' && message ? 'block' : 'none';
        flash.textContent = kind === 'success' ? (message || '') : '';
    }
}

async function loadAdminTeam() {
    if (!canManageUsers()) {
        setTeamMessage('error', 'You do not have permission to manage users.');
        return;
    }
    syncCreateUserButtons();
    try {
        const res = await fetch('/api/admin/staff?include_inactive=1', { credentials: 'same-origin' });
        const data = await res.json();
        const tbody = document.getElementById('adm-team-table-body');
        if (!tbody) return;
        if (!res.ok || data.status !== 'success') {
            throw new Error(data.message || 'Unable to load team users.');
        }
        const staff = data.staff || [];
        tbody.innerHTML = staff.length ? staff.map(s => `
            <tr>
                <td style="font-weight:700;">${escapeHtml(s.full_name)}</td>
                <td>${escapeHtml(s.email)}</td>
                <td>${escapeHtml(s.role)}</td>
                <td class="team-dept-cell">${renderStaffAccessCell(s)}</td>
                <td><span class="status-badge ${s.status === 'Active' ? 'completed' : 'pending'}">${escapeHtml(s.status)}</span></td>
            </tr>
        `).join('') : '<tr><td colspan="5" style="color:#64748b;">No team users yet.</td></tr>';
        lucide.createIcons();
    } catch (err) {
        setTeamMessage('error', err.message || 'Unable to load team users.');
    }
}

function fillCreateUserRoleOptions() {
    const sel = document.getElementById('create-user-role');
    if (!sel) return;
    const allowed = creatableRolesFor(currentUser);
    const allowedSet = new Set(allowed);
    sel.innerHTML = ALL_USER_ROLES.map((role) => {
        const permitted = allowedSet.has(role);
        const disabled = permitted ? '' : ' disabled';
        const label = permitted ? role : `${role} (not permitted)`;
        return `<option value="${escapeHtml(role)}"${disabled}>${escapeHtml(label)}</option>`;
    }).join('');
    if (allowed.includes('STAFF')) sel.value = 'STAFF';
    else if (allowed.length) sel.value = allowed[0];
    fillCreateUserDepartments(STAFF_DEPARTMENTS.slice());
    syncCreateUserDepartmentField();
}

function renderStaffAccessCell(staff) {
    if (!staff || staff.role !== 'STAFF') {
        return '<span class="access-full-badge">Full access</span>';
    }
    return renderDeptMultiSelect(staff.id, staffDepartments(staff), staff.full_name);
}

function renderDeptMultiSelect(staffId, selected, staffName) {
    const chosen = selected || [];
    return `
        <div class="dept-inline" data-staff-id="${staffId}" aria-label="Access for ${escapeHtml(staffName || 'staff')}">
            ${STAFF_DEPARTMENTS.map((dept) => `
                <label class="dept-chip${chosen.includes(dept) ? ' is-on' : ''}">
                    <input type="checkbox" value="${escapeHtml(dept)}" ${chosen.includes(dept) ? 'checked' : ''} onchange="saveStaffDepartments(${staffId}, this.closest('.dept-inline'))">
                    <span>${escapeHtml(dept)}</span>
                </label>
            `).join('')}
        </div>
    `;
}

function fillCreateUserDepartments(selected) {
    const box = document.getElementById('create-user-departments');
    if (!box) return;
    const chosen = selected || [];
    box.innerHTML = STAFF_DEPARTMENTS.map((dept) => `
        <label>
            <input type="checkbox" value="${escapeHtml(dept)}" ${chosen.includes(dept) ? 'checked' : ''}>
            <span>${escapeHtml(dept)}</span>
        </label>
    `).join('');
}

function selectedCreateUserDepartments() {
    return Array.from(document.querySelectorAll('#create-user-departments input[type="checkbox"]:checked')).map((el) => el.value);
}

async function saveStaffDepartments(staffId, wrap) {
    const departments = wrap
        ? Array.from(wrap.querySelectorAll('input[type="checkbox"]:checked')).map((el) => el.value)
        : [];
    if (wrap) {
        wrap.querySelectorAll('.dept-chip').forEach((chip) => {
            const box = chip.querySelector('input[type="checkbox"]');
            chip.classList.toggle('is-on', !!(box && box.checked));
        });
    }
    try {
        const res = await fetch(`/api/admin/staff/${staffId}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'same-origin',
            body: JSON.stringify({ departments })
        });
        const data = await res.json();
        if (!res.ok || data.status !== 'success') {
            throw new Error(data.message || 'Unable to update access.');
        }
        taskStaffCache = [];
    } catch (err) {
        alert(err.message || 'Unable to update access.');
        loadAdminTeam();
    }
}

function syncCreateUserDepartmentField() {
    const role = document.getElementById('create-user-role')?.value;
    const wrap = document.getElementById('create-user-department-wrap');
    if (wrap) wrap.style.display = role === 'STAFF' ? '' : 'none';
}

function openCreateUserModal() {
    if (!canManageUsers()) {
        alert('You do not have permission to create users.');
        return;
    }
    const form = document.getElementById('create-user-form');
    if (form) form.reset();
    const country = document.getElementById('create-user-country');
    if (country) country.value = 'United Kingdom';
    const statusSel = document.getElementById('create-user-status');
    if (statusSel) statusSel.value = 'Active';
    fillCreateUserRoleOptions();
    showModalError('create-user-error', '');
    const modal = document.getElementById('modal-create-user');
    if (modal) modal.classList.add('active');
    lucide.createIcons();
}

function closeCreateUserModal() {
    const modal = document.getElementById('modal-create-user');
    if (modal) modal.classList.remove('active');
    const form = document.getElementById('create-user-form');
    if (form) form.reset();
    const pass = document.getElementById('create-user-password');
    const confirm = document.getElementById('create-user-password-confirm');
    if (pass) pass.value = '';
    if (confirm) confirm.value = '';
    showModalError('create-user-error', '');
}

async function submitCreateUserForm(event) {
    if (event) event.preventDefault();
    if (!canManageUsers()) {
        showModalError('create-user-error', 'You do not have permission to create users.');
        return;
    }
    const password = document.getElementById('create-user-password').value;
    const confirm = document.getElementById('create-user-password-confirm').value;
    if (password !== confirm) {
        showModalError('create-user-error', 'Passwords do not match.');
        return;
    }
    const submitBtn = document.getElementById('create-user-submit');
    if (submitBtn) submitBtn.disabled = true;
    showModalError('create-user-error', '');
    const payload = {
        full_name: document.getElementById('create-user-name').value.trim(),
        email: document.getElementById('create-user-email').value.trim(),
        phone: document.getElementById('create-user-phone').value.trim(),
        country: document.getElementById('create-user-country').value.trim(),
        role: document.getElementById('create-user-role').value,
        departments: document.getElementById('create-user-role').value === 'STAFF' ? selectedCreateUserDepartments() : [],
        status: document.getElementById('create-user-status').value,
        password,
        confirm_password: confirm
    };
    if (!creatableRolesFor(currentUser).includes(payload.role)) {
        showModalError('create-user-error', 'You cannot assign that role.');
        if (submitBtn) submitBtn.disabled = false;
        return;
    }
    try {
        const res = await fetch('/api/admin/staff', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            credentials: 'same-origin',
            body: JSON.stringify(payload)
        });
        const data = await res.json();
        if (!res.ok || data.status !== 'success') {
            throw new Error(data.message || 'Unable to create user.');
        }
        const created = data.user || {};
        closeCreateUserModal();
        const successMsg = `${created.full_name || 'User'} can sign in with ${created.email || 'their email'} and the password you set.`;
        setTeamMessage('success', successMsg);
        taskStaffCache = [];
        if (created.role && created.role !== 'CLIENT') {
            await loadAdminTeam();
        }
        if (created.role === 'CLIENT' || activeView === 'admin-customers') {
            await loadAdminCustomers();
        }
        if (activeView === 'admin-tasks') {
            if (created.role === 'CLIENT') {
                /* stay on current tab */
            }
            await loadAdminTasks();
            if (activeTasksTeamTab === 'team') await loadAdminTeam();
        }
    } catch (err) {
        showModalError('create-user-error', err.message || 'Unable to create user.');
    } finally {
        if (submitBtn) submitBtn.disabled = false;
        lucide.createIcons();
    }
}

async function submitNewTeamUser(event) {
    return submitCreateUserForm(event);
}

let adminCustomersCache = [];

function customerSignupSource(customer) {
    return customer && customer.wordpress_user_id ? 'website' : 'crm';
}

function customerSignupAgeDays(customer) {
    if (!customer || !customer.created_at) return null;
    const created = new Date(String(customer.created_at).replace(' ', 'T'));
    if (Number.isNaN(created.getTime())) return null;
    return (Date.now() - created.getTime()) / 86400000;
}

function renderAdminCustomers() {
    const tbody = document.getElementById('adm-customers-table-body');
    const countEl = document.getElementById('adm-customers-count');
    if (!tbody) return;
    const search = (document.getElementById('filter-admin-customer-search')?.value || '').trim().toLowerCase();
    const source = document.getElementById('filter-admin-customer-source')?.value || '';
    const ageDays = parseInt(document.getElementById('filter-admin-customer-age')?.value || '', 10);
    const rows = adminCustomersCache.filter((c) => {
        if (search) {
            const hay = `${c.full_name || ''} ${c.email || ''}`.toLowerCase();
            if (!hay.includes(search)) return false;
        }
        if (source && customerSignupSource(c) !== source) return false;
        if (ageDays) {
            const age = customerSignupAgeDays(c);
            if (age == null || age > ageDays) return false;
        }
        return true;
    });
    if (countEl) {
        countEl.textContent = `${rows.length} of ${adminCustomersCache.length} accounts`;
    }
    if (!rows.length) {
        tbody.innerHTML = `<tr><td colspan="9" style="text-align:center; color:#64748b; padding:28px;">No customer signups match the current filters.</td></tr>`;
        updateCustomerSelectionState();
        return;
    }
    tbody.innerHTML = rows.map((c) => {
        const sourceKey = customerSignupSource(c);
        const age = customerSignupAgeDays(c);
        const isNew = age != null && age <= 7;
        const sourceLabel = sourceKey === 'website' ? 'Website signup' : 'Created in CRM';
        const portalReady = c.portal_login_ready !== false;
        const portalLabel = portalReady ? 'Can sign in' : 'Needs password';
        return `
            <tr>
                <td style="text-align:center;"><input type="checkbox" class="chk-customer-item" value="${c.id}" onchange="updateCustomerSelectionState()"></td>
                <td style="font-weight:700;">${escapeHtml(c.full_name || '')}${isNew ? ' <span class="signup-new-badge">New</span>' : ''}</td>
                <td>${escapeHtml(c.email || '')}</td>
                <td>${escapeHtml(formatDateTime(c.created_at) || formatDate(c.created_at) || '—')}</td>
                <td>${escapeHtml(sourceLabel)}</td>
                <td>${c.companies_count ?? 0}</td>
                <td>${c.orders_count ?? 0}</td>
                <td><span class="status-badge ${portalReady ? 'completed' : 'pending'}">${portalLabel}</span></td>
                <td class="cell-actions">
                    <div class="table-action-btns">
                        <button type="button" class="btn-primary btn-table" data-customer-action="profile" data-customer-id="${c.id}">View profile</button>
                        ${portalReady ? '' : `<button type="button" class="btn-secondary btn-table" data-customer-action="password" data-customer-id="${c.id}" data-customer-email="${escapeHtml(c.email || '')}">Set password</button>`}
                        <button type="button" class="btn-secondary btn-table" style="color:#dc2626; border-color:#fca5a5; background:#fef2f2;" onclick="deleteCustomerSingle(${c.id}, '${escapeJsString(c.full_name || c.email)}')"><i data-lucide="trash-2"></i> Delete</button>
                    </div>
                </td>
            </tr>
        `;
    }).join('');
    updateCustomerSelectionState();
    if (window.lucide && typeof window.lucide.createIcons === 'function') window.lucide.createIcons();
}

function toggleSelectAllCustomers(masterChk) {
    const isChecked = Boolean(masterChk && masterChk.checked);
    document.querySelectorAll('.chk-customer-item').forEach((chk) => {
        chk.checked = isChecked;
    });
    updateCustomerSelectionState();
}

function updateCustomerSelectionState() {
    const selected = Array.from(document.querySelectorAll('.chk-customer-item:checked'));
    const bulkBtn = document.getElementById('btn-delete-selected-customers');
    const countEl = document.getElementById('selected-customers-count');
    const masterChk = document.getElementById('chk-select-all-customers');
    
    if (countEl) countEl.textContent = selected.length;
    if (bulkBtn) bulkBtn.style.display = selected.length > 0 ? 'inline-flex' : 'none';
    
    const all = document.querySelectorAll('.chk-customer-item');
    if (masterChk && all.length > 0) {
        masterChk.checked = selected.length === all.length;
    }
}

async function deleteCustomerSingle(customerId, customerName) {
    if (!customerId) return;
    if (!confirm(`Are you sure you want to delete customer account "${customerName}"?`)) return;
    try {
        const res = await fetch('/api/admin/customers/delete', {
            method: 'POST',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ customer_id: customerId })
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data.status !== 'success') {
            throw new Error(data.message || 'Could not delete customer.');
        }
        alert(data.message || 'Customer account removed.');
        await loadAdminCustomers();
    } catch (err) {
        alert(err.message || 'Could not delete customer.');
    }
}

async function deleteSelectedCustomers() {
    const selectedBoxes = Array.from(document.querySelectorAll('.chk-customer-item:checked'));
    const selectedIds = selectedBoxes.map((chk) => parseInt(chk.value, 10)).filter(Boolean);
    if (!selectedIds.length) {
        alert('Please select at least one customer to delete.');
        return;
    }
    if (!confirm(`Are you sure you want to delete ${selectedIds.length} selected customer account(s)?`)) return;
    try {
        const res = await fetch('/api/admin/customers/bulk-delete', {
            method: 'POST',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ customer_ids: selectedIds })
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data.status !== 'success') {
            throw new Error(data.message || 'Could not delete selected customers.');
        }
        alert(data.message || 'Selected customer accounts removed.');
        await loadAdminCustomers();
    } catch (err) {
        alert(err.message || 'Could not delete selected customers.');
    }
}

function filterAdminCustomers() {
    renderAdminCustomers();
}

async function setClientPortalPassword(customerId, email) {
    if (!customerId) return;
    const password = window.prompt(`Set portal password for ${email || 'this customer'} (min 10 characters):`);
    if (!password) return;
    const confirm = window.prompt('Confirm the password:');
    if (password !== confirm) {
        window.alert('Passwords do not match.');
        return;
    }
    try {
        const res = await fetch('/api/admin/customers/password', {
            method: 'POST',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ customer_id: customerId, password, confirm_password: confirm }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data.status !== 'success') {
            throw new Error(data.message || 'Could not set the password.');
        }
        window.alert(data.message || 'Portal password saved.');
        await loadAdminCustomers();
    } catch (err) {
        window.alert(err.message || 'Could not set the password.');
    }
}

async function loadAdminCustomers() {
    const tbody = document.getElementById('adm-customers-table-body');
    try {
        if (tbody) {
            tbody.innerHTML = `<tr><td colspan="8" style="text-align:center; color:#64748b; padding:28px;">Loading signups…</td></tr>`;
        }
        const res = await fetch('/api/admin/customers', { credentials: 'same-origin' });
        const data = await res.json();
        if (!res.ok || data.status !== 'success') {
            throw new Error(data.message || 'Unable to load customers.');
        }
        adminCustomersCache = data.customers || [];
        renderAdminCustomers();
    } catch (err) {
        adminCustomersCache = [];
        if (tbody) {
            tbody.innerHTML = `<tr><td colspan="8" style="text-align:center; color:#dc2626; padding:28px;">${escapeHtml(err.message || 'Unable to load customers.')}</td></tr>`;
        }
    }
}

// ----------------------------------------------------
// CENTRALIZED CLIENT CRM RECORD MODAL HANDLERS
// ----------------------------------------------------
let currentCrmClientData = null;

async function openCrmClientModal(clientId) {
    try {
        const res = await fetch(`/api/admin/clients/${clientId}/full`, { credentials: 'same-origin' });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data.status !== 'success' || !data.client) {
            throw new Error(data.message || 'Unable to open customer profile.');
        }
        currentCrmClientData = data;
        const c = data.client;
        const displayName = (c.full_name || c.email || 'Customer').trim();
        const initials = displayName.split(/\s+/).filter(Boolean).map((n) => n[0]).join('').slice(0, 2).toUpperCase() || 'C';

        document.getElementById('crm-modal-name').textContent = displayName;
        document.getElementById('crm-modal-meta').textContent = `WP User ID: ${c.wordpress_user_id || 'N/A'} | ${c.email || ''}`;
        document.getElementById('crm-modal-avatar').textContent = initials;
        const b2bElem = document.getElementById('crm-ov-b2b-id');
        if (b2bElem) b2bElem.textContent = c.b2b_id || 'B2B-000001';
        document.getElementById('crm-ov-email').textContent = c.email || 'N/A';
        document.getElementById('crm-ov-phone').textContent = c.phone || 'N/A';
        document.getElementById('crm-ov-country').textContent = c.country || 'United Kingdom';
        document.getElementById('crm-ov-status').textContent = c.status || '—';
        document.getElementById('crm-ov-synced').textContent = c.last_synced_at ? `Synced (${c.last_synced_at})` : 'Not synced';
        document.getElementById('crm-ov-address').textContent = c.address || 'N/A';

        const compBox = document.getElementById('crm-companies-list');
        if (compBox) {
            const companies = data.companies || [];
            compBox.innerHTML = companies.length ? companies.map((comp) => `
                <div style="padding:10px; border:1px solid var(--border-color); border-radius:8px; margin-bottom:8px;">
                    <strong>${escapeHtml(comp.name || '')}</strong> ${comp.b2b_id ? `<span class="b2b-badge">${escapeHtml(comp.b2b_id)}</span>` : ''} (#${escapeHtml(comp.company_number || '')}) • ${escapeHtml(comp.package || '')}
                    <div style="font-size:0.78rem; color:var(--text-muted);">Office: ${escapeHtml(comp.reg_office || '—')} | Status: ${escapeHtml(comp.status || '')}</div>
                </div>
            `).join('') : '<p style="color:var(--text-muted);">No registered companies for this client.</p>';
        }

        const ordBox = document.getElementById('crm-orders-list');
        if (ordBox) {
            const orders = data.orders || [];
            ordBox.innerHTML = orders.length ? orders.map((o) => {
                const priceBit = canViewRevenue() && o.total != null ? ` (£${parseFloat(o.total).toFixed(2)})` : '';
                return `
                <div style="padding:10px; border:1px solid #e2e8f0; border-radius:8px; margin-bottom:8px; display:flex; justify-content:space-between; gap:8px; align-items:center;">
                    <div>
                        <strong>${escapeHtml(o.order_number)}</strong> — ${escapeHtml(o.service_name)}${priceBit}
                        <div style="font-size:0.78rem; color:#64748b;">${escapeHtml(formatDate(o.created_at))} · ${escapeHtml(o.status)} · ${o.progress_percent}%</div>
                    </div>
                    <div class="table-action-btns">
                        ${canUploadClientDocuments() ? `<button type="button" class="btn-secondary btn-table" onclick="openDeliverDocumentModal({orderId:${o.id}, companyId:${o.company_id || 'null'}})">Upload</button>` : ''}
                        ${canManageOrders() ? `<button type="button" class="btn-secondary btn-table" onclick="closeCrmClientModal(); openStaffOrderWorkspace(${o.id}, { editCheckout: true })">Change details</button>` : ''}
                        <button type="button" class="btn-primary btn-table" onclick="closeCrmClientModal(); openStaffOrderWorkspace(${o.id})">Process</button>
                    </div>
                </div>`;
            }).join('') : '<p style="color:#64748b;">No order history available.</p>';
        }

        const invBox = document.getElementById('crm-invoices-list');
        if (invBox) {
            const invoices = data.invoices || [];
            invBox.innerHTML = invoices.length ? invoices.map((inv) => {
                const amountBit = canViewRevenue() && inv.total != null ? ` — £${parseFloat(inv.total).toFixed(2)}` : '';
                return `
                <div style="padding:10px; border:1px solid #e2e8f0; border-radius:8px; margin-bottom:8px;">
                    <strong>${escapeHtml(inv.invoice_number || '')}</strong>${amountBit} (${escapeHtml(inv.display_status || inv.status || '')})
                </div>`;
            }).join('') : '<p style="color:#64748b;">No invoices available.</p>';
        }

        renderCrmDocumentsList(data.documents || []);
        const uploadBtn = document.getElementById('crm-upload-document-btn');
        if (uploadBtn) uploadBtn.style.display = canUploadClientDocuments() ? 'inline-flex' : 'none';

        const suppBox = document.getElementById('crm-support-list');
        if (suppBox) {
            const tickets = data.support_tickets || [];
            suppBox.innerHTML = tickets.length ? tickets.map((t) => `
                <div style="padding:10px; border:1px solid #e2e8f0; border-radius:8px; margin-bottom:8px;">
                    <strong>Ticket #${escapeHtml(t.ticket_number || '')}</strong>: ${escapeHtml(t.subject || '')} (${escapeHtml(t.status || '')})
                </div>
            `).join('') : '<p style="color:#64748b;">No support tickets filed.</p>';
        }

        const logBox = document.getElementById('crm-logs-list');
        if (logBox) {
            const logs = data.activity_logs || [];
            logBox.innerHTML = logs.length ? logs.map((lg) => `
                <div style="padding:8px; border-bottom:1px solid #f1f5f9; font-size:0.8rem;">
                    <strong>${escapeHtml(lg.action || '')}</strong> • <span style="color:#64748b;">${escapeHtml(formatDate(lg.created_at))}</span>
                    <div style="color:#475569;">${escapeHtml(lg.details || '')}</div>
                </div>
            `).join('') : '<p style="color:#64748b;">No audit trail recorded.</p>';
        }

        switchCrmTab('overview');
        const modal = document.getElementById('crm-client-modal');
        if (modal) modal.classList.add('active');
    } catch (err) {
        console.error(err);
        alert(err.message || 'Unable to open customer profile.');
    }
}

function switchCrmTab(tabName) {
    const tabs = ['overview', 'companies', 'orders', 'invoices', 'documents', 'support', 'logs'];
    tabs.forEach(t => {
        const btn = document.getElementById(`crm-tab-${t}`);
        const pane = document.getElementById(`crm-tab-content-${t}`);
        if (btn) btn.classList.toggle('active', t === tabName);
        if (pane) pane.style.display = t === tabName ? 'block' : 'none';
    });
}

function closeCrmClientModal() {
    const modal = document.getElementById('crm-client-modal');
    if (modal) modal.classList.remove('active');
}

function setCrmDeliverStatus(message) {
    const box = document.getElementById('crm-deliver-doc-status');
    if (!box) return;
    if (!message) {
        box.style.display = 'none';
        box.textContent = '';
        return;
    }
    box.style.display = 'block';
    box.textContent = message;
}

function renderCrmDocumentsList(docs) {
    const docBox = document.getElementById('crm-documents-list');
    if (!docBox) return;
    const list = Array.isArray(docs) ? docs : [];
    if (!list.length) {
        docBox.innerHTML = '<p style="color:#64748b;">No documents uploaded.</p>';
        return;
    }
    const canReview = canUploadClientDocuments();
    docBox.innerHTML = list.map(doc => `
        <div style="padding:10px; border:1px solid #e2e8f0; border-radius:8px; margin-bottom:8px; display:flex; justify-content:space-between; gap:10px; align-items:flex-start;">
            <div>
                <strong>${escapeHtml(doc.name)}</strong>
                <div style="font-size:0.78rem; color:#64748b;">
                    ${escapeHtml(doc.category || '—')}
                    · ${escapeHtml(doc.order_number || 'No order')}
                    · ${escapeHtml(formatDate(doc.created_at))}
                    · ${escapeHtml(doc.uploaded_by || '—')}
                </div>
                <div style="margin-top:6px;"><span class="status-badge ${documentStatusClass(doc.status)}">${escapeHtml(doc.status || '—')}</span></div>
            </div>
            <div style="display:flex; gap:6px; flex-wrap:wrap; align-items:center;">
                <div class="doc-file-actions">
                    ${documentActionButtons(doc)}
                </div>
                ${canReview ? `
                    <select class="select-filter" style="min-width:140px;" onchange="reviewCrmDocument(${doc.id}, this.value)">
                        <option value="Pending Review" ${doc.status === 'Pending Review' ? 'selected' : ''}>Pending Review</option>
                        <option value="Approved" ${doc.status === 'Approved' ? 'selected' : ''}>Approved</option>
                        <option value="Rejected" ${doc.status === 'Rejected' ? 'selected' : ''}>Rejected</option>
                        <option value="Requires Update" ${doc.status === 'Requires Update' ? 'selected' : ''}>Requires Update</option>
                    </select>
                ` : ''}
            </div>
        </div>
    `).join('');
}

async function refreshCrmClientDocuments() {
    if (!currentCrmClientData || !currentCrmClientData.client) return;
    const res = await fetch(`/api/admin/clients/${currentCrmClientData.client.id}/full`);
    const data = await res.json();
    if (!res.ok || data.status !== 'success') {
        throw new Error(data.message || 'Unable to refresh documents.');
    }
    currentCrmClientData = data;
    renderCrmDocumentsList(data.documents || []);
}

async function reviewCrmDocument(docId, status) {
    if (!canUploadClientDocuments()) return;
    try {
        const res = await fetch(`/api/admin/documents/${docId}`, {
            method: 'PUT',
            headers: {'Content-Type': 'application/json'},
            credentials: 'same-origin',
            body: JSON.stringify({ status })
        });
        const data = await res.json();
        if (!res.ok || data.status !== 'success') {
            throw new Error(data.message || 'Unable to update document status.');
        }
        await refreshCrmClientDocuments();
        setCrmDeliverStatus(data.message || 'Document status updated.');
    } catch (err) {
        alert(err.message || 'Unable to update document status.');
    }
}

let deliverDocClientId = null;
let deliverDocAssoc = { companies: [], orders: [] };

function showDeliverDocumentError(message) {
    const box = document.getElementById('deliver-document-error');
    if (!box) return;
    box.style.display = message ? 'block' : 'none';
    box.textContent = message || '';
}

function fillDeliverDocumentSelects(selectedCompanyId, selectedOrderId) {
    const companySel = document.getElementById('deliver-document-company');
    const orderSel = document.getElementById('deliver-document-order');
    const companies = deliverDocAssoc.companies || [];
    const orders = deliverDocAssoc.orders || [];
    if (companySel) {
        companySel.innerHTML = `<option value="">No company</option>` + companies.map(c =>
            `<option value="${c.id}">${escapeHtml(c.name)}</option>`
        ).join('');
        if (selectedCompanyId) companySel.value = String(selectedCompanyId);
    }
    const companyId = companySel ? companySel.value : '';
    const filtered = companyId
        ? orders.filter(o => !o.company_id || String(o.company_id) === String(companyId))
        : orders;
    if (orderSel) {
        orderSel.innerHTML = `<option value="">No order</option>` + filtered.map(o =>
            `<option value="${o.id}">${escapeHtml(o.order_number)} — ${escapeHtml(o.service_name || '')}</option>`
        ).join('');
        if (selectedOrderId) orderSel.value = String(selectedOrderId);
    }
}

function onDeliverDocumentCompanyChange() {
    fillDeliverDocumentSelects(document.getElementById('deliver-document-company')?.value || '', '');
}

async function loadDeliverDocumentAssociations(clientId) {
    if (currentCrmClientData && currentCrmClientData.client && String(currentCrmClientData.client.id) === String(clientId)) {
        return { companies: currentCrmClientData.companies || [], orders: currentCrmClientData.orders || [] };
    }
    const res = await fetch(`/api/admin/clients/${clientId}/full`);
    const data = await res.json();
    if (!res.ok || data.status !== 'success') {
        throw new Error(data.message || 'Unable to load customer companies and orders.');
    }
    return { companies: data.companies || [], orders: data.orders || [], client: data.client };
}

function setDeliverDocumentClientMode(pickClient) {
    const locked = document.getElementById('deliver-document-client-locked');
    const pick = document.getElementById('deliver-document-client-pick');
    if (locked) locked.style.display = pickClient ? 'none' : 'block';
    if (pick) pick.style.display = pickClient ? 'block' : 'none';
}

function onDeliverDocumentFileChange() {
    const fileInput = document.getElementById('deliver-document-file');
    const nameInput = document.getElementById('deliver-document-name');
    const file = fileInput && fileInput.files ? fileInput.files[0] : null;
    if (!file || !nameInput || nameInput.value.trim()) return;
    nameInput.value = file.name.replace(/\.[^.]+$/, '') || file.name;
}

async function onDeliverDocumentClientChange() {
    const select = document.getElementById('deliver-document-client-select');
    const clientId = select ? select.value : '';
    showDeliverDocumentError('');
    if (!clientId) {
        deliverDocClientId = null;
        deliverDocAssoc = { companies: [], orders: [] };
        fillDeliverDocumentSelects('', '');
        return;
    }
    try {
        const assoc = await loadDeliverDocumentAssociations(clientId);
        deliverDocClientId = Number(clientId);
        deliverDocAssoc = { companies: assoc.companies || [], orders: assoc.orders || [] };
        fillDeliverDocumentSelects('', '');
    } catch (err) {
        deliverDocClientId = null;
        showDeliverDocumentError(err.message || 'Unable to load this customer.');
    }
}

async function openDeliverDocumentModalFromReview() {
    if (!canUploadClientDocuments()) {
        alert('You do not have permission to upload documents.');
        return;
    }
    await openDeliverDocumentModal({ pickClient: true });
}

async function openDeliverDocumentModal(prefill) {
    if (!canUploadClientDocuments()) {
        alert('You do not have permission to upload documents.');
        return;
    }
    const options = prefill || {};
    const client = (currentCrmClientData && currentCrmClientData.client) || null;
    const clientId = options.clientId || (client && client.id);
    showDeliverDocumentError('');
    const form = document.getElementById('deliver-document-form');
    if (form) form.reset();
    const pickSelect = document.getElementById('deliver-document-client-select');
    try {
        if (options.pickClient) {
            setDeliverDocumentClientMode(true);
            deliverDocClientId = null;
            deliverDocAssoc = { companies: [], orders: [] };
            fillDeliverDocumentSelects('', '');
            const customers = await fetchTaskCustomers();
            if (pickSelect) {
                pickSelect.innerHTML = `<option value="">Select a customer</option>` + (customers || []).map((c) =>
                    `<option value="${c.id}">${escapeHtml(c.full_name || 'Customer')}${c.email ? ' (' + escapeHtml(c.email) + ')' : ''}</option>`
                ).join('');
            }
            document.getElementById('modal-deliver-document').classList.add('active');
            safeCreateIcons();
            return;
        }
        if (!clientId) {
            alert('Open a customer record or choose a customer first.');
            return;
        }
        setDeliverDocumentClientMode(false);
        if (pickSelect) pickSelect.innerHTML = `<option value="">Select a customer</option>`;
        const assoc = await loadDeliverDocumentAssociations(clientId);
        deliverDocClientId = clientId;
        deliverDocAssoc = { companies: assoc.companies || [], orders: assoc.orders || [] };
        const label = document.getElementById('deliver-document-client');
        const clientName = (assoc.client && assoc.client.full_name) || (client && client.full_name) || 'Customer';
        const clientEmail = (assoc.client && assoc.client.email) || (client && client.email) || '';
        if (label) label.value = clientEmail ? `${clientName} (${clientEmail})` : clientName;
        fillDeliverDocumentSelects(options.companyId || '', options.orderId || '');
        document.getElementById('modal-deliver-document').classList.add('active');
        safeCreateIcons();
    } catch (err) {
        alert(err.message || 'Unable to open upload form.');
    }
}

function openDeliverDocumentModalFromOrder() {
    if (!canOperateOrderDocuments()) {
        setStaffOrderNotice('error', 'You do not have permission to upload documents.');
        return;
    }
    if (!staffOrderClientId) {
        setStaffOrderNotice('error', 'This order has no linked customer, so a document cannot be saved.');
        return;
    }
    openDeliverDocumentModal({
        clientId: staffOrderClientId,
        companyId: staffOrderCompanyId,
        orderId: staffOrderId
    });
}

function closeDeliverDocumentModal() {
    const modal = document.getElementById('modal-deliver-document');
    if (modal) modal.classList.remove('active');
}

function readFileAsBase64(file) {
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result).split(',')[1] || '');
        reader.onerror = () => reject(new Error('Unable to read the selected file.'));
        reader.readAsDataURL(file);
    });
}

function formatDeliverSuccess(data) {
    const lines = ['✓ Document uploaded successfully'];
    if (data.notification_created) lines.push('✓ Client notification created');
    if (data.email_sent || data.email_status === 'queued' || data.email_status) {
        lines.push('✓ Email notification queued');
    }
    return lines.join('\n');
}

async function submitDeliverDocumentForm(event) {
    event.preventDefault();
    if (!canUploadClientDocuments()) return;
    if (!deliverDocClientId) {
        showDeliverDocumentError('Select a customer first.');
        return;
    }
    showDeliverDocumentError('');
    const nameInput = document.getElementById('deliver-document-name');
    const fileInput = document.getElementById('deliver-document-file');
    const name = nameInput ? nameInput.value.trim() : '';
    const file = fileInput && fileInput.files ? fileInput.files[0] : null;
    if (!name) {
        showDeliverDocumentError('Document name is required.');
        return;
    }
    if (!file) {
        showDeliverDocumentError('Choose a file to upload.');
        return;
    }
    const visibleCheckbox = document.getElementById('deliver-document-client-visible');
    const clientVisible = visibleCheckbox ? (visibleCheckbox.checked ? 1 : 0) : 1;
    const submitBtn = document.getElementById('deliver-document-submit');
    if (submitBtn) submitBtn.disabled = true;
    try {
        const b64 = await readFileAsBase64(file);
        const payload = {
            client_id: deliverDocClientId,
            name,
            file_name: file.name,
            category: document.getElementById('deliver-document-category')?.value || 'Order Documents',
            client_message: document.getElementById('deliver-document-message')?.value || '',
            file_content_base64: b64,
            client_visible: clientVisible
        };
        const companyId = document.getElementById('deliver-document-company')?.value;
        const orderId = document.getElementById('deliver-document-order')?.value;
        if (companyId) payload.company_id = Number(companyId);
        if (orderId) payload.order_id = Number(orderId);
        const res = await fetch('/api/admin/documents', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            credentials: 'same-origin',
            body: JSON.stringify(payload)
        });
        const data = await res.json();
        if (!res.ok || data.status !== 'success') {
            throw new Error(data.message || 'Upload failed.');
        }
        closeDeliverDocumentModal();
        const statusText = formatDeliverSuccess(data);
        setCrmDeliverStatus(statusText.replace(/\n/g, ' '));
        if (currentCrmClientData && currentCrmClientData.client && String(currentCrmClientData.client.id) === String(deliverDocClientId)) {
            await refreshCrmClientDocuments();
            switchCrmTab('documents');
        }
        if (staffOrderId) {
            setStaffOrderNotice('success', statusText.replace(/\n/g, ' '));
            await openStaffOrderWorkspace(staffOrderId);
        }
        if (typeof loadAdminDocuments === 'function') loadAdminDocuments();
        loadNotificationsCount();
    } catch (err) {
        showDeliverDocumentError(err.message || 'Upload failed.');
    } finally {
        if (submitBtn) submitBtn.disabled = false;
    }
}

let activeAdminDocumentsLifecycle = 'READY_FOR_APPROVAL';
let adminDocumentsSelection = new Set();
let adminDocumentsCache = [];
let documentTriageCache = null;

function switchAdminDocumentsLifecycle(stage) {
    activeAdminDocumentsLifecycle = stage || 'READY_FOR_APPROVAL';
    document.querySelectorAll('#adm-documents-lifecycle-tabs .portfolio-tab').forEach((btn) => {
        btn.classList.toggle('active', btn.getAttribute('data-lifecycle') === activeAdminDocumentsLifecycle);
    });
    clearAdminDocumentsSelection();
    loadAdminDocuments();
}

function confidencePill(label, value) {
    const n = Number(value);
    const cls = n >= 75 ? 'is-high' : (n >= 50 ? 'is-mid' : 'is-low');
    const shown = Number.isFinite(n) ? `${Math.round(n)}%` : '—';
    return `<span class="confidence-pill ${cls}">${escapeHtml(label)} ${shown}</span>`;
}

function updateAdminDocumentsSelectionCount() {
    const el = document.getElementById('adm-docs-selection-count');
    if (el) el.textContent = adminDocumentsSelection.size ? `${adminDocumentsSelection.size} selected` : '';
}

function toggleAdminDocumentSelection(docId, checked) {
    const id = Number(docId);
    if (!id) return;
    if (checked) adminDocumentsSelection.add(id);
    else adminDocumentsSelection.delete(id);
    updateAdminDocumentsSelectionCount();
}

function toggleAdminDocumentsPageSelection(checked) {
    (adminDocumentsCache || []).forEach((d) => {
        const id = Number(d.id);
        if (!id) return;
        if (checked) adminDocumentsSelection.add(id);
        else adminDocumentsSelection.delete(id);
    });
    document.querySelectorAll('.adm-doc-select').forEach((cb) => {
        cb.checked = checked;
    });
    updateAdminDocumentsSelectionCount();
}

function clearAdminDocumentsSelection() {
    adminDocumentsSelection = new Set();
    const pageCb = document.getElementById('adm-docs-select-page');
    if (pageCb) pageCb.checked = false;
    document.querySelectorAll('.adm-doc-select').forEach((cb) => { cb.checked = false; });
    updateAdminDocumentsSelectionCount();
}

async function loadAdminDocuments() {
    const tbody = document.getElementById('adm-documents-table-body');
    const errBox = document.getElementById('adm-documents-error');
    if (errBox) {
        errBox.style.display = 'none';
        errBox.textContent = '';
    }
    if (tbody) {
        tbody.innerHTML = `<tr><td colspan="7" style="text-align:center; color:#64748b; padding:28px;">Loading documents…</td></tr>`;
    }
    try {
        const stage = activeAdminDocumentsLifecycle || 'READY_FOR_APPROVAL';
        const res = await fetch(`/api/admin/documents/lifecycle?stage=${encodeURIComponent(stage)}`, { credentials: 'same-origin' });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data.status !== 'success') {
            throw new Error('Unable to load documents.');
        }
        const counts = data.counts || {};
        document.querySelectorAll('.lifecycle-count').forEach((el) => {
            const key = el.getAttribute('data-count');
            el.textContent = String(counts[key] != null ? counts[key] : 0);
        });
        const docs = Array.isArray(data.documents) ? data.documents : [];
        adminDocumentsCache = docs;
        if (!tbody) return;
        if (!docs.length) {
            tbody.innerHTML = `<tr><td colspan="7" style="text-align:center; padding:36px 20px;">
                <div style="font-weight:800; color:#0f172a; margin-bottom:8px;">No documents in this stage</div>
                <div style="color:#64748b; font-size:0.85rem; max-width:440px; margin:0 auto 16px;">Customer uploads appear here after triage. Posted Documents only include approved files.</div>
            </td></tr>`;
            return;
        }
        tbody.innerHTML = docs.map((d) => {
            const id = Number(d.id);
            const checked = adminDocumentsSelection.has(id) ? ' checked' : '';
            const canAct = d.lifecycle_status !== 'POSTED_DOCUMENTS' && !d.is_posted;
            return `
                <tr>
                    <td>${canAct ? `<input type="checkbox" class="adm-doc-select" data-doc-id="${id}"${checked} onchange="toggleAdminDocumentSelection(${id}, this.checked)">` : ''}</td>
                    <td>${escapeHtml(d.client_name || '—')}<div style="font-size:0.72rem; color:#64748b;">${escapeHtml(d.client_email || '')}</div></td>
                    <td style="font-weight:700;">${escapeHtml(d.name || 'Document')}<div style="font-size:0.72rem;color:#64748b;">${escapeHtml(d.category || '')}</div></td>
                    <td>${escapeHtml(d.company_name || '—')}<div style="font-size:0.72rem;color:#64748b;">${escapeHtml(d.company_number || '')}</div></td>
                    <td><div class="confidence-pills">
                        ${confidencePill('OCR', d.ocr_confidence)}
                        ${confidencePill('ID', d.identity_confidence)}
                        ${confidencePill('Co', d.company_match_confidence)}
                        ${Number(d.duplicate_confidence) >= 100 ? confidencePill('Dup', d.duplicate_confidence) : ''}
                    </div></td>
                    <td><span class="status-badge ${d.lifecycle_status === 'POSTED_DOCUMENTS' || d.is_posted ? 'completed' : (d.lifecycle_status === 'QUARANTINE' ? 'cancelled' : 'pending')}">${escapeHtml(d.lifecycle_status || '')}</span></td>
                    <td class="cell-actions">
                        <div class="order-row-actions table-action-btns">
                            ${documentActionButtons(d)}
                            <button type="button" class="btn-secondary btn-table" onclick="openDocumentTriageModal(${id})">Review</button>
                            ${canAct ? `<button type="button" class="btn-primary btn-table" onclick="approveLifecycleDocument(${id})">Approve</button>` : ''}
                        </div>
                    </td>
                </tr>
            `;
        }).join('');
        updateAdminDocumentsSelectionCount();
    } catch (err) {
        console.error(err);
        if (errBox) {
            errBox.style.display = 'block';
            errBox.textContent = 'Unable to load documents. Please try again.';
        }
        if (tbody) {
            tbody.innerHTML = `<tr><td colspan="7" style="text-align:center; color:#dc2626; padding:28px;">Unable to load documents.</td></tr>`;
        }
    }
}

function closeDocumentTriageModal() {
    const modal = document.getElementById('modal-document-triage');
    if (modal) modal.classList.remove('active');
    documentTriageCache = null;
}

function openDocumentTriageModal(docId) {
    const doc = (adminDocumentsCache || []).find((d) => Number(d.id) === Number(docId));
    if (!doc) return;
    documentTriageCache = doc;
    const body = document.getElementById('doc-triage-body');
    const actions = document.getElementById('doc-triage-actions');
    const meta = doc.match_meta || {};
    const matching = doc.matching_evidence || meta.reasons || [];
    const conflicting = doc.conflicting_evidence || [];
    const tops = doc.top_candidates || meta.top_candidates || [];
    if (body) {
        body.innerHTML = `
            <div style="display:grid;gap:12px;">
                <div><strong>${escapeHtml(doc.name || 'Document')}</strong>
                    <div style="font-size:0.82rem;color:#64748b;">${escapeHtml(doc.category || '')} · ${escapeHtml(doc.file_type || '')} · ${escapeHtml(doc.file_size || '')}</div>
                </div>
                <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;">
                    <div><div style="font-size:0.75rem;color:#64748b;">Customer</div><div>${escapeHtml(doc.client_name || '—')} (#${escapeHtml(String(doc.user_id || ''))})</div></div>
                    <div><div style="font-size:0.75rem;color:#64748b;">Company</div><div>${escapeHtml(doc.company_name || '—')} ${doc.company_number ? '(' + escapeHtml(doc.company_number) + ')' : ''}</div></div>
                </div>
                <div class="confidence-pills">
                    ${confidencePill('OCR', doc.ocr_confidence)}
                    ${confidencePill('Class', doc.classification_confidence)}
                    ${confidencePill('Identity', doc.identity_confidence)}
                    ${confidencePill('Customer', doc.customer_match_confidence)}
                    ${confidencePill('Company', doc.company_match_confidence)}
                    ${confidencePill('Duplicate', doc.duplicate_confidence)}
                </div>
                <div style="font-size:0.82rem;">Score gap Δ: <strong>${escapeHtml(String(doc.score_gap != null ? doc.score_gap : (meta.score_gap != null ? meta.score_gap : '—')))}</strong></div>
                <div>
                    <div style="font-weight:700;margin-bottom:4px;">Matching evidence</div>
                    <ul style="margin:0;padding-left:18px;color:#166534;">${(matching.length ? matching : ['—']).map((x) => `<li>${escapeHtml(String(x))}</li>`).join('')}</ul>
                </div>
                <div>
                    <div style="font-weight:700;margin-bottom:4px;">Conflicting evidence</div>
                    <ul style="margin:0;padding-left:18px;color:#991b1b;">${(conflicting.length ? conflicting : ['—']).map((x) => `<li>${escapeHtml(String(x))}</li>`).join('')}</ul>
                </div>
                ${tops.length ? `<div><div style="font-weight:700;margin-bottom:4px;">Top candidates</div>
                    <ul style="margin:0;padding-left:18px;">${tops.slice(0, 5).map((t) => `<li>${escapeHtml(t.name || '')} (${escapeHtml(t.company_number || '')}) — ${escapeHtml(String(t.score != null ? t.score : ''))}%</li>`).join('')}</ul>
                </div>` : ''}
                <div style="font-size:0.82rem;color:#64748b;">${escapeHtml(doc.review_notes || '')}</div>
            </div>
        `;
    }
    const canAct = doc.lifecycle_status !== 'POSTED_DOCUMENTS' && !doc.is_posted;
    if (actions) {
        actions.innerHTML = `
            ${canAct ? `<button type="button" class="btn-primary" onclick="approveLifecycleDocument(${doc.id})">Approve &amp; Post</button>` : ''}
            ${canAct ? `<button type="button" class="btn-secondary" onclick="rejectLifecycleDocument(${doc.id})">Reject / Quarantine</button>` : ''}
            ${canAct ? `<button type="button" class="btn-secondary" onclick="reassignLifecycleDocument(${doc.id})">Reassign</button>` : ''}
            ${documentActionButtons(doc)}
        `;
    }
    const modal = document.getElementById('modal-document-triage');
    if (modal) modal.classList.add('active');
}

async function approveLifecycleDocument(docId) {
    try {
        const res = await fetch(`/api/admin/documents/${docId}/approve`, {
            method: 'POST',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: '{}',
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data.status !== 'success') throw new Error(data.message || 'Approve failed');
        closeDocumentTriageModal();
        clearAdminDocumentsSelection();
        loadAdminDocuments();
    } catch (err) {
        alert(err.message || 'Approve failed');
    }
}

async function rejectLifecycleDocument(docId) {
    const reason = window.prompt('Rejection reason', 'Rejected by staff') || 'Rejected by staff';
    try {
        const res = await fetch(`/api/admin/documents/${docId}/reject`, {
            method: 'POST',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ reason }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data.status !== 'success') throw new Error(data.message || 'Reject failed');
        closeDocumentTriageModal();
        loadAdminDocuments();
    } catch (err) {
        alert(err.message || 'Reject failed');
    }
}

async function reassignLifecycleDocument(docId) {
    const clientId = window.prompt('Client user id (blank to keep)');
    const companyId = window.prompt('Company id (blank to keep)');
    const category = window.prompt('Document category (blank to keep)');
    const payload = { reprocess: true };
    if (clientId && String(clientId).trim()) payload.client_id = Number(clientId);
    if (companyId && String(companyId).trim()) payload.company_id = Number(companyId);
    if (category && String(category).trim()) payload.category = String(category).trim();
    try {
        const res = await fetch(`/api/admin/documents/${docId}/reassign`, {
            method: 'POST',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data.status !== 'success') throw new Error(data.message || 'Reassign failed');
        closeDocumentTriageModal();
        loadAdminDocuments();
    } catch (err) {
        alert(err.message || 'Reassign failed');
    }
}

async function pollBulkApprovalJob(jobId) {
    const box = document.getElementById('adm-documents-job-status');
    for (let i = 0; i < 40; i += 1) {
        await new Promise((r) => setTimeout(r, 500));
        const res = await fetch(`/api/admin/documents/bulk-jobs/${encodeURIComponent(jobId)}`, { credentials: 'same-origin' });
        const data = await res.json().catch(() => ({}));
        const job = data.job || {};
        if (box) {
            box.style.display = 'block';
            box.textContent = `Bulk job ${jobId}: ${job.status || '…'} — approved ${job.approved_count || 0}, skipped ${job.skipped_count || 0}, failed ${job.failed_count || 0}`;
        }
        if (job.status === 'COMPLETED' || job.status === 'FAILED') {
            loadAdminDocuments();
            return;
        }
    }
}

async function bulkApproveAdminDocuments(overrideReview) {
    const ids = Array.from(adminDocumentsSelection);
    if (!ids.length) {
        alert('Select one or more documents first.');
        return;
    }
    try {
        const res = await fetch('/api/admin/documents/bulk-approve', {
            method: 'POST',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                document_ids: ids,
                override_review_required: Boolean(overrideReview),
            }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data.status !== 'success') throw new Error(data.message || 'Bulk approve failed');
        clearAdminDocumentsSelection();
        if (data.job_id) await pollBulkApprovalJob(data.job_id);
        else loadAdminDocuments();
    } catch (err) {
        alert(err.message || 'Bulk approve failed');
    }
}

async function bulkQuarantineAdminDocuments() {
    const ids = Array.from(adminDocumentsSelection);
    if (!ids.length) {
        alert('Select one or more documents first.');
        return;
    }
    const reason = window.prompt('Quarantine reason', 'Bulk quarantined by staff') || 'Bulk quarantined by staff';
    try {
        const res = await fetch('/api/admin/documents/bulk-quarantine', {
            method: 'POST',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ document_ids: ids, reason }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data.status !== 'success') throw new Error(data.message || 'Bulk quarantine failed');
        clearAdminDocumentsSelection();
        loadAdminDocuments();
    } catch (err) {
        alert(err.message || 'Bulk quarantine failed');
    }
}

async function approveDocument(docId) {
    return approveLifecycleDocument(docId);
}

async function loadAdminLogs() {
    try {
        const res = await fetch('/api/admin/activity-logs');
        const data = await res.json();
        const tbody = document.getElementById('adm-logs-table-body');
        if (tbody && data.status === 'success') {
            tbody.innerHTML = data.logs.map(l => `
                <tr>
                    <td>${formatDate(l.created_at)}</td>
                    <td style="font-weight:700; color:var(--brand-primary);">${l.user_email}</td>
                    <td><code>${l.action}</code></td>
                    <td>${l.details}</td>
                </tr>
            `).join('');
        }
    } catch (err) { console.error(err); }
}

async function loadAdminServices() {
    const tbody = document.getElementById('adm-services-table-body');
    const errBox = document.getElementById('adm-services-error');
    const addBtn = document.getElementById('btn-admin-add-service');
    if (addBtn) addBtn.style.display = canManageServiceCatalog() ? 'inline-flex' : 'none';
    if (errBox) {
        errBox.style.display = 'none';
        errBox.textContent = '';
    }
    if (tbody) {
        tbody.innerHTML = `<tr><td colspan="6" style="text-align:center; color:#64748b; padding:28px;">Loading services…</td></tr>`;
    }
    try {
        const res = await fetch('/api/admin/services', { credentials: 'same-origin' });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data.status !== 'success') {
            throw new Error(data.message || 'Unable to load services.');
        }
        const services = Array.isArray(data.services) ? data.services : [];
        adminServicesCache = services;
        if (!tbody) return;
        if (!services.length) {
            tbody.innerHTML = `<tr><td colspan="6" style="text-align:center; padding:36px 20px;">
                <div style="font-weight:800; color:#0f172a; margin-bottom:8px;">No services in the CRM catalog yet</div>
                <div style="color:#64748b; font-size:0.85rem; max-width:440px; margin:0 auto 16px;">Website products still show on orders. Add a service here to list it in the CRM catalog.</div>
                ${canManageServiceCatalog() ? '<button type="button" class="btn-primary" onclick="openCreateServiceModal()">Add Service</button>' : ''}
            </td></tr>`;
            return;
        }
        const canEdit = canManageServiceCatalog();
        tbody.innerHTML = services.map((s) => {
            const sid = Number(s.id);
            const actions = canEdit
                ? `<div class="order-row-actions table-action-btns">
                        <button type="button" class="btn-secondary btn-table" onclick="openEditServiceModal(${sid})">Edit</button>
                        <button type="button" class="portfolio-delete-btn" title="Delete" aria-label="Delete service" onclick="deleteAdminService(${sid})"><i data-lucide="trash-2"></i></button>
                   </div>`
                : '—';
            return `
            <tr data-service-id="${sid}">
                <td><strong>${escapeHtml(s.name || 'Service')}</strong><div style="font-size:0.75rem; color:#64748b;">${escapeHtml(s.description || '')}</div></td>
                <td>${escapeHtml(s.category || '—')}</td>
                <td>£${parseFloat(s.price || 0).toFixed(2)}</td>
                <td>${escapeHtml(s.duration || '—')}</td>
                <td><span class="status-badge ${s.status === 'Active' ? 'completed' : 'pending'}">${escapeHtml(s.status || '')}</span></td>
                <td>${actions}</td>
            </tr>`;
        }).join('');
        if (window.lucide) lucide.createIcons();
    } catch (err) {
        console.error(err);
        if (errBox) {
            errBox.style.display = 'block';
            errBox.textContent = err.message || 'Unable to load services.';
        }
        if (tbody) {
            tbody.innerHTML = `<tr><td colspan="6" style="text-align:center; color:#dc2626; padding:28px;">Unable to load services.</td></tr>`;
        }
    }
}

let adminServicesCache = [];

function setServiceModalMode(isEdit) {
    const title = document.getElementById('service-modal-title');
    const subtitle = document.getElementById('service-modal-subtitle');
    const submitBtn = document.getElementById('create-service-submit');
    const statusWrap = document.getElementById('create-service-status-wrap');
    if (title) title.textContent = isEdit ? 'Edit Service' : 'Add Service';
    if (subtitle) {
        subtitle.textContent = isEdit
            ? 'Update CRM catalog price and details. Website WooCommerce prices stay unchanged until you sync products.'
            : 'This adds a service to the CRM catalog. It does not create a website product.';
    }
    if (submitBtn) submitBtn.textContent = isEdit ? 'Save changes' : 'Save Service';
    if (statusWrap) statusWrap.style.display = isEdit ? 'block' : 'none';
}

function openCreateServiceModal() {
    if (!canManageServiceCatalog()) return;
    const err = document.getElementById('create-service-error');
    if (err) {
        err.style.display = 'none';
        err.textContent = '';
    }
    const form = document.getElementById('create-service-form');
    if (form) form.reset();
    const idField = document.getElementById('create-service-id');
    if (idField) idField.value = '';
    const cat = document.getElementById('create-service-category');
    if (cat && !cat.value) cat.value = 'Corporate';
    const dur = document.getElementById('create-service-duration');
    if (dur && !dur.value) dur.value = '12 Months';
    const status = document.getElementById('create-service-status');
    if (status) status.value = 'Active';
    setServiceModalMode(false);
    const modal = document.getElementById('modal-create-service');
    if (modal) modal.classList.add('active');
    safeCreateIcons();
}

function openEditServiceModal(serviceId) {
    if (!canManageServiceCatalog()) return;
    const sid = Number(serviceId);
    const service = (adminServicesCache || []).find((item) => Number(item.id) === sid);
    if (!service) return;
    const err = document.getElementById('create-service-error');
    if (err) {
        err.style.display = 'none';
        err.textContent = '';
    }
    const idField = document.getElementById('create-service-id');
    if (idField) idField.value = String(sid);
    const nameEl = document.getElementById('create-service-name');
    if (nameEl) nameEl.value = service.name || '';
    const catEl = document.getElementById('create-service-category');
    if (catEl) catEl.value = service.category || 'Corporate';
    const descEl = document.getElementById('create-service-description');
    if (descEl) descEl.value = service.description || '';
    const priceEl = document.getElementById('create-service-price');
    if (priceEl) priceEl.value = parseFloat(service.price || 0).toFixed(2);
    const durEl = document.getElementById('create-service-duration');
    if (durEl) durEl.value = service.duration || '12 Months';
    const statusEl = document.getElementById('create-service-status');
    if (statusEl) statusEl.value = service.status === 'Inactive' ? 'Inactive' : 'Active';
    setServiceModalMode(true);
    const modal = document.getElementById('modal-create-service');
    if (modal) modal.classList.add('active');
    safeCreateIcons();
}

function closeCreateServiceModal() {
    const modal = document.getElementById('modal-create-service');
    if (modal) modal.classList.remove('active');
}

async function submitCreateServiceForm(event) {
    event.preventDefault();
    if (!canManageServiceCatalog()) return;
    const err = document.getElementById('create-service-error');
    if (err) {
        err.style.display = 'none';
        err.textContent = '';
    }
    const serviceId = Number(document.getElementById('create-service-id')?.value || 0);
    const isEdit = Boolean(serviceId);
    const payload = {
        name: document.getElementById('create-service-name')?.value.trim() || '',
        category: document.getElementById('create-service-category')?.value.trim() || 'Corporate',
        description: document.getElementById('create-service-description')?.value.trim() || '',
        price: document.getElementById('create-service-price')?.value,
        duration: document.getElementById('create-service-duration')?.value.trim() || '12 Months',
    };
    if (isEdit) {
        payload.status = document.getElementById('create-service-status')?.value || 'Active';
    }
    const submitBtn = document.getElementById('create-service-submit');
    if (submitBtn) submitBtn.disabled = true;
    try {
        const res = await fetch(isEdit ? `/api/admin/services/${serviceId}` : '/api/admin/services', {
            method: isEdit ? 'PUT' : 'POST',
            headers: {'Content-Type': 'application/json'},
            credentials: 'same-origin',
            body: JSON.stringify(payload)
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data.status !== 'success') {
            throw new Error(data.message || (isEdit ? 'Unable to update service.' : 'Unable to create service.'));
        }
        closeCreateServiceModal();
        await loadAdminServices();
    } catch (ex) {
        if (err) {
            err.style.display = 'block';
            err.textContent = ex.message || (isEdit ? 'Unable to update service.' : 'Unable to create service.');
        }
    } finally {
        if (submitBtn) submitBtn.disabled = false;
    }
}

async function deleteAdminService(serviceId) {
    if (!canManageServiceCatalog()) {
        alert('You do not have permission to delete services.');
        return;
    }
    const sid = Number(serviceId);
    const service = (adminServicesCache || []).find((item) => Number(item.id) === sid);
    const name = (service && service.name) || 'this service';
    if (!confirm(`Delete ${name} from the CRM catalog? Existing orders keep their history.`)) return;
    try {
        const res = await fetch(`/api/admin/services/${sid}`, {
            method: 'DELETE',
            credentials: 'same-origin',
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data.status !== 'success') {
            throw new Error(data.message || 'Unable to delete service.');
        }
        document.querySelectorAll(`[data-service-id="${sid}"]`).forEach((el) => el.remove());
        adminServicesCache = (adminServicesCache || []).filter((item) => Number(item.id) !== sid);
        await loadAdminServices();
    } catch (err) {
        alert(err.message || 'Unable to delete service.');
    }
}

let adminInvoicesCache = [];
let pendingEditInvoiceId = null;

function invoicePaymentMethodOptions(selected) {
    const modes = ['Website Charge', 'PKR(Bank Transfer)', 'GBP(Bank Transfer)', 'Credit Card (Stripe)'];
    if (selected && !modes.includes(selected)) modes.unshift(selected);
    return modes.map((mode) => `<option value="${escapeHtml(mode)}"${mode === (selected || '') ? ' selected' : ''}>${escapeHtml(mode)}</option>`).join('');
}

function invoiceStatusBadgeClass(status) {
    if (status === 'Paid') return 'completed';
    if (status === 'Partial Paid' || status === 'Part paid') return 'in-progress';
    if (status === 'Overdue' || status === 'Cancelled') return 'cancelled';
    return 'pending';
}

function invoiceWhenLabel(inv) {
    if (!inv) return 'After work';
    if (inv.payment_timing === 'Deposit') {
        const deposit = parseFloat(inv.deposit_amount || 0);
        return deposit > 0 ? `Deposit £${deposit.toFixed(2)}` : 'Deposit';
    }
    return inv.payment_timing === 'Advance' ? 'Advance' : 'After work';
}

function invoiceDisplayStatus(inv) {
    if (!inv) return 'Pending';
    if (inv.display_status === 'Part paid') return 'Partial Paid';
    if (inv.display_status) return inv.display_status;
    if (inv.status === 'Paid' || inv.status === 'Overdue' || inv.status === 'Cancelled' || inv.status === 'Partial Paid') {
        return inv.status;
    }
    const paid = parseFloat(inv.amount_paid || 0);
    const total = parseFloat(inv.total || 0);
    if (paid > 0.004 && paid + 0.004 < total) return 'Partial Paid';
    return inv.status || 'Pending';
}

function fillStaffOrderPaymentPlan(invoice, order) {
    const card = document.getElementById('staff-order-pay-plan');
    if (!card) return;
    const show = canManageOrders() && canViewRevenue() && invoice && invoice.id;
    card.hidden = !show;
    if (!show) return;
    staffOrderInvoice = invoice;
    const timingEl = document.getElementById('staff-order-pay-timing');
    const depositEl = document.getElementById('staff-order-deposit-amount');
    const total = parseFloat((invoice.total != null ? invoice.total : order && order.total) || 0);
    const timing = invoice.payment_timing === 'Advance' || invoice.payment_timing === 'Deposit' ? invoice.payment_timing : 'After work';
    if (timingEl) timingEl.value = timing;
    const deposit = parseFloat(invoice.deposit_amount || 0) || Math.round(total * 50) / 100;
    if (depositEl) {
        depositEl.value = deposit.toFixed(2);
        depositEl.oninput = () => toggleStaffOrderDepositField();
    }
    toggleStaffOrderDepositField();
    const paid = parseFloat(invoice.amount_paid || 0);
    const due = invoice.amount_due != null ? parseFloat(invoice.amount_due) : Math.max(0, total - paid);
    const summary = document.getElementById('staff-order-pay-summary');
    if (summary) {
        summary.textContent = `${invoice.invoice_number || 'Invoice'} · ${invoiceDisplayStatus(invoice)} · received £${paid.toFixed(2)} · due £${due.toFixed(2)}`;
    }
    const markDeposit = document.getElementById('staff-order-mark-deposit');
    const markPaid = document.getElementById('staff-order-mark-paid');
    if (markDeposit) markDeposit.style.display = (timing === 'Deposit' && due > 0.004) ? 'inline-flex' : 'none';
    if (markPaid) markPaid.style.display = due > 0.004 ? 'inline-flex' : 'none';
}

function toggleStaffOrderDepositField() {
    const timing = ((document.getElementById('staff-order-pay-timing') || {}).value || '');
    const wrap = document.getElementById('staff-order-deposit-wrap');
    const hint = document.getElementById('staff-order-deposit-hint');
    const invoice = staffOrderInvoice || {};
    const total = parseFloat(invoice.total || (staffOrderRecord && staffOrderRecord.total) || 0);
    if (wrap) wrap.hidden = timing !== 'Deposit';
    if (hint && timing === 'Deposit') {
        const raw = parseFloat(((document.getElementById('staff-order-deposit-amount') || {}).value || '').replace(/[^0-9.]/g, ''));
        const deposit = Number.isFinite(raw) ? raw : Math.round(total * 50) / 100;
        hint.textContent = `Balance later: £${Math.max(0, total - deposit).toFixed(2)}`;
    }
}

async function saveStaffOrderPaymentPlan(extra) {
    const invoice = staffOrderInvoice || {};
    if (!invoice.id) return;
    const timing = ((document.getElementById('staff-order-pay-timing') || {}).value || 'After work').trim();
    const payload = Object.assign({
        payment_timing: timing,
        payment_method: ((document.getElementById('staff-order-payment-mode') || {}).value || '').trim(),
    }, extra || {});
    if (timing === 'Deposit') {
        payload.deposit_amount = ((document.getElementById('staff-order-deposit-amount') || {}).value || '').trim();
    }
    try {
        const res = await fetch(`/api/admin/invoices/${invoice.id}`, {
            method: 'PUT',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data.status !== 'success') throw new Error(data.message || 'Unable to save the payment plan.');
        if (staffOrderId) await openStaffOrderWorkspace(staffOrderId);
        const mailNote = data.email_sent ? ' Payment email sent to the company owner.' : '';
        setStaffOrderNotice('success', extra && extra.send_payment_details
            ? `Payment details emailed to the company owner.${mailNote}`
            : ((data.invoice && (data.invoice.display_status === 'Partial Paid' || data.invoice.display_status === 'Part paid'))
                ? `Deposit recorded.${mailNote}`
                : `Payment plan saved.${mailNote}`));
        if (typeof loadAdminInvoices === 'function') loadAdminInvoices();
    } catch (err) {
        setStaffOrderNotice('error', err.message || 'Unable to save the payment plan.');
    }
}

function markStaffOrderDepositReceived() {
    const amount = ((document.getElementById('staff-order-deposit-amount') || {}).value || '').trim();
    return saveStaffOrderPaymentPlan({ payment_timing: 'Deposit', deposit_amount: amount, amount_paid: amount });
}

function markStaffOrderPaidInFull() {
    return saveStaffOrderPaymentPlan({ status: 'Paid' });
}

function emailStaffOrderPaymentDetails() {
    return saveStaffOrderPaymentPlan({ send_payment_details: true });
}

function openStaffOrderInvoice() {
    const id = staffOrderInvoice && staffOrderInvoice.id;
    if (id) openInvoiceDocument(id);
}

function toggleEditInvoiceDepositField() {
    const timing = ((document.getElementById('edit-invoice-timing') || {}).value || '');
    const wrap = document.getElementById('edit-invoice-deposit-wrap');
    if (wrap) wrap.hidden = timing !== 'Deposit';
}

function openInvoiceDocument(invoiceId) {
    const id = Number(invoiceId);
    if (!id) return;
    window.open(`/api/admin/invoices/${id}/document`, '_blank', 'noopener');
}

function closeEditInvoiceModal() {
    const modal = document.getElementById('modal-edit-invoice');
    if (modal) modal.classList.remove('active');
    pendingEditInvoiceId = null;
}

function openEditInvoiceModal(invoiceId) {
    const invoice = (adminInvoicesCache || []).find((row) => Number(row.id) === Number(invoiceId));
    if (!invoice) return;
    pendingEditInvoiceId = Number(invoice.id);
    const numEl = document.getElementById('edit-invoice-number');
    const clientEl = document.getElementById('edit-invoice-client');
    const statusEl = document.getElementById('edit-invoice-status');
    const timingEl = document.getElementById('edit-invoice-timing');
    const methodEl = document.getElementById('edit-invoice-method');
    const dueEl = document.getElementById('edit-invoice-due');
    const totalEl = document.getElementById('edit-invoice-total');
    const err = document.getElementById('edit-invoice-error');
    if (numEl) numEl.textContent = invoice.invoice_number || 'Invoice';
    if (clientEl) {
        const bits = [invoice.owner_name || invoice.client_name, invoice.owner_form_email || invoice.client_email, invoice.order_number].filter(Boolean);
        clientEl.textContent = bits.join(' · ');
    }
    if (statusEl) {
        const stored = invoice.status || 'Pending';
        const display = invoiceDisplayStatus(invoice);
        statusEl.value = (stored === 'Partial Paid' || stored === 'Part paid' || display === 'Partial Paid' || display === 'Part paid')
            ? 'Partial Paid'
            : stored;
    }
    if (timingEl) {
        timingEl.value = (invoice.payment_timing === 'Advance' || invoice.payment_timing === 'Deposit')
            ? invoice.payment_timing
            : 'After work';
    }
    if (methodEl) methodEl.innerHTML = invoicePaymentMethodOptions(invoice.payment_method || '');
    if (dueEl) dueEl.value = String(invoice.due_date || '').slice(0, 10);
    if (totalEl) {
        totalEl.value = invoice.total != null ? parseFloat(invoice.total || 0).toFixed(2) : '';
        totalEl.disabled = !canEditOrderPrice();
    }
    const depositEl = document.getElementById('edit-invoice-deposit');
    if (depositEl) {
        const fallback = Math.round(parseFloat(invoice.total || 0) * 50) / 100;
        depositEl.value = parseFloat(invoice.deposit_amount || fallback || 0).toFixed(2);
    }
    const paidEl = document.getElementById('edit-invoice-paid');
    if (paidEl) paidEl.value = parseFloat(invoice.amount_paid || 0).toFixed(2);
    toggleEditInvoiceDepositField();
    if (err) {
        err.style.display = 'none';
        err.textContent = '';
    }
    applyAdminOrderFinanceVisibility();
    const modal = document.getElementById('modal-edit-invoice');
    if (modal) modal.classList.add('active');
    if (window.lucide) lucide.createIcons();
}

async function submitEditInvoiceForm(event) {
    event.preventDefault();
    if (!pendingEditInvoiceId) return;
    const err = document.getElementById('edit-invoice-error');
    const submitBtn = document.getElementById('edit-invoice-submit');
    const payload = {
        status: ((document.getElementById('edit-invoice-status') || {}).value || '').trim(),
        payment_timing: ((document.getElementById('edit-invoice-timing') || {}).value || '').trim(),
        payment_method: ((document.getElementById('edit-invoice-method') || {}).value || '').trim(),
        due_date: ((document.getElementById('edit-invoice-due') || {}).value || '').trim(),
    };
    if (canEditOrderPrice()) {
        payload.total = ((document.getElementById('edit-invoice-total') || {}).value || '').trim();
    }
    if (payload.payment_timing === 'Deposit') {
        payload.deposit_amount = ((document.getElementById('edit-invoice-deposit') || {}).value || '').trim();
    }
    payload.amount_paid = ((document.getElementById('edit-invoice-paid') || {}).value || '').trim();
    if (err) {
        err.style.display = 'none';
        err.textContent = '';
    }
    if (submitBtn) submitBtn.disabled = true;
    try {
        const res = await fetch(`/api/admin/invoices/${pendingEditInvoiceId}`, {
            method: 'PUT',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data.status !== 'success') {
            throw new Error(data.message || 'Unable to update this invoice.');
        }
        closeEditInvoiceModal();
        await loadAdminInvoices();
        const box = document.getElementById('adm-invoices-error');
        if (box) {
            if (data.email_sent) {
                box.style.display = 'block';
                box.style.background = '#ecfdf5';
                box.style.color = '#047857';
                box.textContent = 'Invoice saved. Email sent to the company owner.';
            } else if (payload.status === 'Paid') {
                box.style.display = 'block';
                box.style.background = '#fff7ed';
                box.style.color = '#9a3412';
                box.textContent = data.email_status
                    ? `Invoice saved. Email: ${data.email_status}`
                    : 'Invoice saved. No payment email was sent.';
            }
        }
    } catch (ex) {
        if (err) {
            err.style.display = 'block';
            err.textContent = ex.message || 'Unable to update this invoice.';
        }
    } finally {
        if (submitBtn) submitBtn.disabled = false;
    }
}

async function emailAdminInvoice(invoiceId) {
    const id = Number(invoiceId || pendingEditInvoiceId || 0);
    if (!id) return;
    const box = document.getElementById('adm-invoices-error');
    const sendBtn = document.getElementById('edit-invoice-send');
    if (sendBtn) sendBtn.disabled = true;
    try {
        const res = await fetch(`/api/admin/invoices/${id}`, {
            method: 'PUT',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ send_payment_details: true }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data.status !== 'success') {
            throw new Error(data.message || data.email_status || 'Unable to send this invoice.');
        }
        if (!data.email_sent) {
            throw new Error(data.email_status || 'Invoice email was not sent.');
        }
        if (pendingEditInvoiceId) closeEditInvoiceModal();
        await loadAdminInvoices();
        if (box) {
            box.style.display = 'block';
            box.style.background = '#ecfdf5';
            box.style.color = '#047857';
            const inv = data.invoice || {};
            const to = inv.owner_form_email || inv.client_email || 'the company owner';
            box.textContent = `Invoice emailed to ${to}.`;
        }
    } catch (err) {
        if (box) {
            box.style.display = 'block';
            box.style.background = '#fef2f2';
            box.style.color = '#dc2626';
            box.textContent = err.message || 'Unable to send this invoice.';
        } else {
            alert(err.message || 'Unable to send this invoice.');
        }
    } finally {
        if (sendBtn) sendBtn.disabled = false;
    }
}

async function loadAdminInvoices() {
    const tbody = document.getElementById('adm-invoices-table-body');
    const errBox = document.getElementById('adm-invoices-error');
    const showFinance = canViewRevenue();
    const colCount = showFinance ? 8 : 7;
    if (errBox) {
        errBox.style.display = 'none';
        errBox.textContent = '';
        errBox.style.background = '';
        errBox.style.color = '';
    }
    if (tbody) {
        tbody.innerHTML = `<tr><td colspan="${colCount}" style="text-align:center; color:#64748b; padding:28px;">Loading invoices…</td></tr>`;
    }
    try {
        const res = await fetch('/api/admin/invoices', { credentials: 'same-origin' });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data.status !== 'success') {
            throw new Error(data.message || 'Unable to load invoices.');
        }
        const invoices = Array.isArray(data.invoices) ? data.invoices : [];
        adminInvoicesCache = invoices;
        if (!tbody) return;
        if (!invoices.length) {
            tbody.innerHTML = `<tr><td colspan="${colCount}" style="text-align:center; color:#64748b; padding:28px;">No invoices yet.</td></tr>`;
            return;
        }
        tbody.innerHTML = invoices.map((inv) => `
            <tr>
                <td style="font-weight:700;">${escapeHtml(inv.invoice_number || '—')}</td>
                <td>${escapeHtml(inv.owner_name || inv.client_name || '—')}<div style="font-size:0.72rem; color:#64748b;">${escapeHtml(inv.owner_form_email || inv.client_email || '')}</div></td>
                <td>${escapeHtml(inv.order_number || '—')}</td>
                ${showFinance ? `<td class="cell-inv-total">£${parseFloat(inv.total || 0).toFixed(2)}</td>` : ''}
                <td>${escapeHtml(invoiceWhenLabel(inv))}</td>
                <td><span class="status-badge ${invoiceStatusBadgeClass(invoiceDisplayStatus(inv))}">${escapeHtml(invoiceDisplayStatus(inv))}</span></td>
                <td>${escapeHtml(formatDate(inv.created_at))}</td>
                <td class="cell-actions">
                    <div class="table-action-btns">
                        <button type="button" class="btn-secondary btn-table" onclick="openInvoiceDocument(${Number(inv.id)})">Invoice</button>
                        <button type="button" class="btn-secondary btn-table" onclick="emailAdminInvoice(${Number(inv.id)})">Send</button>
                        <button type="button" class="btn-secondary btn-table" onclick="openEditInvoiceModal(${Number(inv.id)})">Edit</button>
                    </div>
                </td>
            </tr>
        `).join('');
        if (window.lucide) lucide.createIcons();
    } catch (err) {
        console.error(err);
        if (errBox) {
            errBox.style.display = 'block';
            errBox.textContent = err.message || 'Unable to load invoices.';
        }
        if (tbody) {
            tbody.innerHTML = `<tr><td colspan="${colCount}" style="text-align:center; color:#dc2626; padding:28px;">Unable to load invoices.</td></tr>`;
        }
    }
}

let accountancyCache = [];
let accountancyBooksState = { companyId: 0, periodEnd: '', clientId: 0 };

function accountancyStatusClass(status) {
    const value = String(status || '').toLowerCase();
    if (value === 'filed') return 'completed';
    if (value === 'overdue') return 'cancelled';
    if (value === 'in progress' || value === 'ready to file' || value === 'requested') return 'in-progress';
    return 'pending';
}

function accountancyVerifyClass(ok, status) {
    if (ok) return 'acct-badge is-ok';
    const value = String(status || '').toLowerCase();
    if (value === 'failed' || value === 'update required') return 'acct-badge is-block';
    if (value === 'in progress') return 'acct-badge is-warn';
    return 'acct-badge is-warn';
}

function accountancyAddressBadge(readiness) {
    const address = (readiness && readiness.address) || {};
    const text = address.value || '';
    if (address.ok) return `<span class="acct-badge is-ok" title="${escapeHtml(text)}">On file</span>`;
    if (text) return `<span class="acct-badge is-warn" title="${escapeHtml(text)}">Check</span>`;
    return '<span class="acct-badge is-block">Missing</span>';
}

function accountancyIssueBadge(readiness) {
    const blocking = Number((readiness && readiness.blocking_count) || 0);
    const total = Number((readiness && readiness.issue_count) || 0);
    const titles = ((readiness && readiness.issues) || []).map((item) => item.title).filter(Boolean).join('; ');
    if (!total) return '<span class="acct-badge is-ok">Clear</span>';
    if (blocking) return `<span class="acct-badge is-block" title="${escapeHtml(titles)}">${blocking} block</span>`;
    return `<span class="acct-badge is-warn" title="${escapeHtml(titles)}">${total} warn</span>`;
}

function accountancyVerifyBadge(node) {
    const item = node || {};
    const label = item.ok ? 'Verified' : (item.status || 'Not started');
    return `<span class="${accountancyVerifyClass(item.ok, item.status)}">${escapeHtml(label)}</span>`;
}

function formatGbpAmount(amount) {
    const value = Number(amount || 0);
    const abs = Math.abs(value).toLocaleString('en-GB', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    return value < 0 ? `−£${abs}` : `£${abs}`;
}

function accountancyOutflowCategory(category) {
    return ['Cost of sales', 'Staff costs', 'Other operating charges', 'Tax', 'Dividends / drawings', 'Bank charges', 'VAT'].includes(category);
}

function accountancyCompanyOptionLabel(row) {
    const number = row.company_number ? ` (${row.company_number})` : '';
    return `${row.name || 'Company'}${number}`;
}

function fillFileAccountsCompanyOptions(selectedId) {
    const select = document.getElementById('file-accounts-company');
    if (!select) return;
    const options = ['<option value="">Select company</option>'].concat(
        (accountancyCache || []).map((row) => `<option value="${Number(row.id)}">${escapeHtml(accountancyCompanyOptionLabel(row))}</option>`)
    );
    select.innerHTML = options.join('');
    if (selectedId) select.value = String(selectedId);
}

function setFileAccountsCompanyLocked(locked, companyId) {
    const select = document.getElementById('file-accounts-company');
    const lockedEl = document.getElementById('file-accounts-company-locked');
    const row = (accountancyCache || []).find((item) => Number(item.id) === Number(companyId));
    if (select) {
        select.hidden = Boolean(locked && companyId);
        select.required = !select.hidden;
        if (companyId) select.value = String(companyId);
    }
    if (lockedEl) {
        lockedEl.hidden = !(locked && companyId);
        lockedEl.textContent = row ? accountancyCompanyOptionLabel(row) : '';
    }
}

function applyAccountancyCompanyPeriod() {
    const select = document.getElementById('file-accounts-company');
    const companyId = Number((select || {}).value || 0);
    const row = (accountancyCache || []).find((item) => Number(item.id) === companyId);
    const current = (row && row.current) || {};
    const setVal = (id, value) => {
        const el = document.getElementById(id);
        if (el && value) el.value = String(value).slice(0, 10);
    };
    setVal('file-accounts-start', current.period_start);
    setVal('file-accounts-end', current.period_end);
    setVal('file-accounts-due', current.due_date);
    const typeEl = document.getElementById('file-accounts-type');
    if (typeEl && current.accounts_type) typeEl.value = current.accounts_type;
    const statusEl = document.getElementById('file-accounts-status');
    if (statusEl) {
        const status = current.status && current.status !== 'Overdue' ? current.status : 'In progress';
        if (Array.from(statusEl.options).some((option) => option.value === status)) statusEl.value = status;
    }
    const confirmEl = document.getElementById('file-accounts-confirmation');
    if (confirmEl) confirmEl.value = current.confirmation_number || '';
    const notesEl = document.getElementById('file-accounts-notes');
    if (notesEl) notesEl.value = current.notes || '';
    const idEl = document.getElementById('file-accounts-id');
    if (idEl) idEl.value = current.id || '';
    applyFileAccountsReadiness();
}

function applyFileAccountsReadiness() {
    const select = document.getElementById('file-accounts-company');
    const companyId = Number((select || {}).value || 0);
    const row = (accountancyCache || []).find((item) => Number(item.id) === companyId);
    const readiness = (row && row.readiness) || {};
    const banner = document.getElementById('file-accounts-readiness');
    const statusEl = document.getElementById('file-accounts-status');
    const canFile = Boolean(readiness.can_file);
    if (statusEl) {
        Array.from(statusEl.options).forEach((option) => {
            if (option.value === 'Ready to file') option.disabled = !canFile;
        });
        if (statusEl.value === 'Ready to file' && !canFile) statusEl.value = 'In progress';
    }
    if (!banner) return;
    banner.hidden = true;
    banner.innerHTML = '';
}

function openFileAccountsModal(companyId) {
    if (!isAdminShellUser(currentUser)) return;
    const err = document.getElementById('file-accounts-error');
    if (err) {
        err.style.display = 'none';
        err.textContent = '';
    }
    const open = () => {
        fillFileAccountsCompanyOptions(companyId);
        setFileAccountsCompanyLocked(Boolean(companyId), companyId);
        if (companyId) applyAccountancyCompanyPeriod();
        const modal = document.getElementById('modal-file-accounts');
        if (modal) modal.classList.add('active');
        if (window.lucide) lucide.createIcons();
    };
    if (!(accountancyCache || []).length) {
        loadAdminAccountancy().then(open).catch(open);
        return;
    }
    open();
}

function closeFileAccountsModal() {
    const modal = document.getElementById('modal-file-accounts');
    if (modal) modal.classList.remove('active');
    setFileAccountsCompanyLocked(false);
}

async function submitFileAccountsForm(event) {
    event.preventDefault();
    const err = document.getElementById('file-accounts-error');
    const submitBtn = document.getElementById('file-accounts-submit');
    const companyId = Number((document.getElementById('file-accounts-company') || {}).value || 0);
    const filingId = Number((document.getElementById('file-accounts-id') || {}).value || 0);
    const payload = {
        company_id: companyId,
        period_start: ((document.getElementById('file-accounts-start') || {}).value || '').trim(),
        period_end: ((document.getElementById('file-accounts-end') || {}).value || '').trim(),
        due_date: ((document.getElementById('file-accounts-due') || {}).value || '').trim(),
        accounts_type: ((document.getElementById('file-accounts-type') || {}).value || 'Micro-entity').trim(),
        status: ((document.getElementById('file-accounts-status') || {}).value || 'In progress').trim(),
        confirmation_number: ((document.getElementById('file-accounts-confirmation') || {}).value || '').trim(),
        notes: ((document.getElementById('file-accounts-notes') || {}).value || '').trim(),
    };
    if (err) {
        err.style.display = 'none';
        err.textContent = '';
    }
    if (!payload.company_id) {
        if (err) {
            err.style.display = 'block';
            err.textContent = 'Select a company.';
        }
        return;
    }
    if (submitBtn) submitBtn.disabled = true;
    try {
        const url = filingId ? `/api/admin/accountancy/${filingId}` : '/api/admin/accountancy';
        const res = await fetch(url, {
            method: filingId ? 'PUT' : 'POST',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data.status !== 'success') {
            throw new Error(data.message || 'Could not save the accounts filing.');
        }
        closeFileAccountsModal();
        await loadAdminAccountancy();
    } catch (ex) {
        if (err) {
            err.style.display = 'block';
            err.textContent = ex.message || 'Could not save the accounts filing.';
        }
    } finally {
        if (submitBtn) submitBtn.disabled = false;
    }
}

async function requestCompanyAccountsFiling(companyId) {
    try {
        const res = await fetch('/api/client/accountancy', {
            method: 'POST',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ company_id: Number(companyId) }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data.status !== 'success') {
            throw new Error(data.message || 'Could not request accounts filing.');
        }
        await loadClientAccountancy();
    } catch (err) {
        alert(err.message || 'Could not request accounts filing.');
    }
}

function setAccountancyBooksError(message) {
    const err = document.getElementById('acct-books-error');
    if (!err) return;
    if (!message) {
        err.hidden = true;
        err.textContent = '';
        return;
    }
    err.hidden = false;
    err.textContent = message;
}

function fillAccountancyBooksWorkspace(data) {
    const company = (data && data.company) || {};
    const current = (data && data.current) || {};
    const readiness = (data && data.readiness) || {};
    accountancyBooksState = {
        companyId: Number(company.id || 0),
        periodEnd: data.period_end || current.period_end || '',
        clientId: Number(company.client_id || 0),
    };
    const title = document.getElementById('acct-books-title');
    if (title) title.textContent = company.name || 'Accounts workspace';
    const subtitle = document.getElementById('acct-books-subtitle');
    if (subtitle) {
        const yearEnd = formatUkNumericDate(accountancyBooksState.periodEnd) || 'this period';
        const directorBit = [company.director, company.director_email || company.client_email].filter(Boolean).join(' · ');
        subtitle.textContent = `${company.company_number || ''}${directorBit ? ` · ${directorBit}` : ''} · year end ${yearEnd} · bank statements to Companies House filing.`;
    }
    const identityEl = document.getElementById('acct-books-identity');
    if (identityEl) identityEl.value = (readiness.identity && readiness.identity.status) || 'Not started';
    const pscEl = document.getElementById('acct-books-psc');
    if (pscEl) pscEl.value = (readiness.psc && readiness.psc.status) || 'Not started';
    const checks = [
        { label: 'Registered office', ok: Boolean(readiness.address && readiness.address.ok), detail: (readiness.address && readiness.address.value) || 'Missing' },
        { label: 'Identity verification', ok: Boolean(readiness.identity && readiness.identity.ok), detail: (readiness.identity && readiness.identity.status) || 'Not started' },
        { label: 'PSC register', ok: Boolean(readiness.psc && readiness.psc.ok), detail: (readiness.psc && readiness.psc.status) || 'Not started' },
        { label: 'Authentication code', ok: Boolean(readiness.authentication_code && readiness.authentication_code.ok), detail: (readiness.authentication_code && readiness.authentication_code.ok) ? 'On file' : 'Missing' },
        { label: 'Personal 11 dijits Code', ok: Boolean(readiness.personal_code && readiness.personal_code.ok), detail: (readiness.personal_code && readiness.personal_code.ok) ? 'On file' : 'Missing' },
        { label: 'UTR', ok: Boolean(readiness.utr && readiness.utr.ok), detail: (readiness.utr && readiness.utr.ok) ? 'On file' : 'Missing' },
    ];
    const checksEl = document.getElementById('acct-books-checks');
    if (checksEl) {
        checksEl.innerHTML = checks.map((item) => `
            <div class="acct-check ${item.ok ? 'is-ok' : 'is-block'}">
                <span>${escapeHtml(item.label)}</span>
                <strong>${escapeHtml(item.detail)}</strong>
            </div>
        `).join('');
    }
    const issues = Array.isArray(readiness.issues) ? readiness.issues : [];
    const issuesEl = document.getElementById('acct-books-issues');
    if (issuesEl) {
        issuesEl.innerHTML = issues.length
            ? issues.map((issue) => `<div class="acct-issue ${issue.severity === 'block' ? 'is-block' : 'is-warn'}"><strong>${escapeHtml(issue.title || '')}</strong> ${escapeHtml(issue.detail || '')}</div>`).join('')
            : '<p class="acct-issue-clear">UK filing checks are complete for this period.</p>';
    }
    const statements = Array.isArray(data.statements) ? data.statements : [];
    const statementsEl = document.getElementById('acct-books-statements');
    if (statementsEl) {
        statementsEl.innerHTML = statements.length
            ? statements.map((doc) => `
                <button type="button" class="acct-statement-item" onclick="openDocumentPreview(${Number(doc.id)})">
                    <strong>${escapeHtml(doc.name || 'Bank statement')}</strong>
                    <span>${escapeHtml(doc.file_size || '')} · ${escapeHtml(formatUkNumericDate((doc.created_at || '').slice(0, 10)) || '')}</span>
                </button>
            `).join('')
            : '<p class="acct-related-empty">No bank statements on file for this company yet.</p>';
    }
    const totals = (data && data.totals) || {};
    const totalsEl = document.getElementById('acct-books-totals');
    if (totalsEl) {
        const cats = Array.isArray(totals.by_category) ? totals.by_category : [];
        totalsEl.innerHTML = `
            <div class="acct-total-pill">In ${escapeHtml(formatGbpAmount(totals.inflows))}</div>
            <div class="acct-total-pill">Out ${escapeHtml(formatGbpAmount(totals.outflows))}</div>
            <div class="acct-total-pill is-net">Net ${escapeHtml(formatGbpAmount(totals.net))}</div>
            ${cats.map((item) => `<div class="acct-total-pill">${escapeHtml(item.category)} ${escapeHtml(formatGbpAmount(item.amount))}</div>`).join('')}
        `;
    }
    const entries = Array.isArray(data.entries) ? data.entries : [];
    const body = document.getElementById('acct-books-entries-body');
    if (body) {
        body.innerHTML = entries.length
            ? entries.map((entry) => `
                <tr>
                    <td>${escapeHtml(formatUkNumericDate(entry.entry_date) || '—')}</td>
                    <td>${escapeHtml(entry.description || '')}</td>
                    <td>${escapeHtml(entry.category || 'Other')}</td>
                    <td>${escapeHtml(formatGbpAmount(entry.amount))}</td>
                    <td>${escapeHtml(entry.source === 'bank_statement' ? 'Statement' : 'Manual')}</td>
                    <td><div class="table-action-btns"><button type="button" class="btn-secondary btn-table" onclick="deleteAccountancyBookEntry(${Number(entry.id)})">Remove</button></div></td>
                </tr>
            `).join('')
            : `<tr><td colspan="6" style="text-align:center; color:#64748b; padding:20px;">No cash book lines yet. Post each bank statement line here.</td></tr>`;
    }
    const dateEl = document.getElementById('acct-books-entry-date');
    if (dateEl && !dateEl.value) dateEl.value = new Date().toISOString().slice(0, 10);
    const fileBtn = document.getElementById('acct-books-file-btn');
    if (fileBtn) fileBtn.textContent = readiness.can_file ? 'File accounts' : 'File anyway';
    if (window.lucide) lucide.createIcons();
}

async function refreshAccountancyBooks() {
    const companyId = Number(accountancyBooksState.companyId || 0);
    if (!companyId) return;
    const params = accountancyBooksState.periodEnd ? `?period_end=${encodeURIComponent(accountancyBooksState.periodEnd)}` : '';
    const res = await fetch(`/api/admin/accountancy/${companyId}/books${params}`, { credentials: 'same-origin' });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || data.status !== 'success') {
        throw new Error(data.message || 'Unable to load the cash book.');
    }
    fillAccountancyBooksWorkspace(data);
}

async function openAccountancyBooks(companyId) {
    if (!isAdminShellUser(currentUser)) return;
    setAccountancyBooksError('');
    accountancyBooksState.companyId = Number(companyId || 0);
    const modal = document.getElementById('modal-accountancy-books');
    if (modal) modal.classList.add('active');
    if (window.lucide) lucide.createIcons();
    try {
        await refreshAccountancyBooks();
    } catch (err) {
        setAccountancyBooksError(err.message || 'Unable to load the cash book.');
    }
}

function closeAccountancyBooks() {
    const modal = document.getElementById('modal-accountancy-books');
    if (modal) modal.classList.remove('active');
}

async function saveAccountancyVerification() {
    const companyId = Number(accountancyBooksState.companyId || 0);
    if (!companyId) return;
    const saveBtn = document.getElementById('acct-books-save-verify');
    if (saveBtn) saveBtn.disabled = true;
    setAccountancyBooksError('');
    try {
        const res = await fetch(`/api/admin/companies/${companyId}`, {
            method: 'PUT',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                identity_verified: ((document.getElementById('acct-books-identity') || {}).value || 'Not started').trim(),
                psc_verified: ((document.getElementById('acct-books-psc') || {}).value || 'Not started').trim(),
            }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data.status !== 'success') {
            throw new Error(data.message || 'Could not save verification.');
        }
        await refreshAccountancyBooks();
        await loadAdminAccountancy();
    } catch (err) {
        setAccountancyBooksError(err.message || 'Could not save verification.');
    } finally {
        if (saveBtn) saveBtn.disabled = false;
    }
}

async function uploadAccountancyBankStatement(event) {
    event.preventDefault();
    const companyId = Number(accountancyBooksState.companyId || 0);
    const fileInput = document.getElementById('acct-books-file');
    const statusEl = document.getElementById('acct-books-upload-status');
    const submitBtn = document.getElementById('acct-books-upload-btn');
    const file = fileInput && fileInput.files ? fileInput.files[0] : null;
    if (!companyId || !file) {
        if (statusEl) statusEl.textContent = 'Choose a bank statement file.';
        return;
    }
    if (submitBtn) submitBtn.disabled = true;
    if (statusEl) statusEl.textContent = 'Uploading…';
    try {
        const b64 = await readFileAsBase64(file);
        const payload = {
            company_id: companyId,
            name: file.name,
            file_name: file.name,
            category: 'Bank statement',
            client_message: 'Your accountant uploaded a bank statement to your company file.',
            file_content_base64: b64,
        };
        if (accountancyBooksState.clientId) payload.client_id = accountancyBooksState.clientId;
        const res = await fetch('/api/admin/documents', {
            method: 'POST',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data.status !== 'success') {
            throw new Error(data.message || 'Could not upload the bank statement.');
        }
        if (fileInput) fileInput.value = '';
        if (statusEl) statusEl.textContent = 'Statement uploaded.';
        await refreshAccountancyBooks();
        await loadAdminAccountancy();
    } catch (err) {
        if (statusEl) statusEl.textContent = err.message || 'Upload failed.';
    } finally {
        if (submitBtn) submitBtn.disabled = false;
    }
}

async function submitAccountancyBookEntry(event) {
    event.preventDefault();
    const companyId = Number(accountancyBooksState.companyId || 0);
    if (!companyId) return;
    const submitBtn = document.getElementById('acct-books-entry-submit');
    const category = ((document.getElementById('acct-books-entry-category') || {}).value || 'Other').trim();
    let direction = ((document.getElementById('acct-books-entry-direction') || {}).value || 'in').trim();
    if (direction === 'in' && accountancyOutflowCategory(category)) direction = 'out';
    const payload = {
        period_end: accountancyBooksState.periodEnd,
        entry_date: ((document.getElementById('acct-books-entry-date') || {}).value || '').trim(),
        description: ((document.getElementById('acct-books-entry-desc') || {}).value || '').trim(),
        category,
        amount: ((document.getElementById('acct-books-entry-amount') || {}).value || '').trim(),
        direction,
        source: 'bank_statement',
    };
    setAccountancyBooksError('');
    if (submitBtn) submitBtn.disabled = true;
    try {
        const res = await fetch(`/api/admin/accountancy/${companyId}/books`, {
            method: 'POST',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data.status !== 'success') {
            throw new Error(data.message || 'Could not add the cash book line.');
        }
        const descEl = document.getElementById('acct-books-entry-desc');
        const amountEl = document.getElementById('acct-books-entry-amount');
        if (descEl) descEl.value = '';
        if (amountEl) amountEl.value = '';
        fillAccountancyBooksWorkspace(data);
        await loadAdminAccountancy();
    } catch (err) {
        setAccountancyBooksError(err.message || 'Could not add the cash book line.');
    } finally {
        if (submitBtn) submitBtn.disabled = false;
    }
}

async function deleteAccountancyBookEntry(entryId) {
    const companyId = Number(accountancyBooksState.companyId || 0);
    if (!companyId || !entryId) return;
    setAccountancyBooksError('');
    try {
        const res = await fetch(`/api/admin/accountancy/${companyId}/books/${Number(entryId)}`, {
            method: 'DELETE',
            credentials: 'same-origin',
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data.status !== 'success') {
            throw new Error(data.message || 'Could not remove the cash book line.');
        }
        fillAccountancyBooksWorkspace(data);
        await loadAdminAccountancy();
    } catch (err) {
        setAccountancyBooksError(err.message || 'Could not remove the cash book line.');
    }
}

function fileAccountsFromBooks() {
    const companyId = Number(accountancyBooksState.companyId || 0);
    closeAccountancyBooks();
    if (companyId) openFileAccountsModal(companyId);
}

async function loadAdminAccountancy() {
    const tbody = document.getElementById('adm-accountancy-table-body');
    const errBox = document.getElementById('adm-accountancy-error');
    if (errBox) {
        errBox.style.display = 'none';
        errBox.textContent = '';
    }
    if (tbody) {
        tbody.innerHTML = `<tr><td colspan="11" style="text-align:center; color:#64748b; padding:28px;">Loading companies…</td></tr>`;
    }
    try {
        const res = await fetch('/api/admin/accountancy', { credentials: 'same-origin' });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data.status !== 'success') {
            throw new Error(data.message || 'Unable to load accountancy.');
        }
        const stats = data.stats || {};
        const setStat = (id, value) => {
            const el = document.getElementById(id);
            if (el) el.textContent = Number(value || 0);
        };
        setStat('accountancy-stat-overdue', stats.overdue);
        setStat('accountancy-stat-due', stats.due_soon);
        setStat('accountancy-stat-progress', stats.in_progress);
        setStat('accountancy-stat-issues', stats.issues);
        setStat('accountancy-stat-filed', stats.filed);
        const rows = Array.isArray(data.companies) ? data.companies : [];
        accountancyCache = rows;
        if (!tbody) return;
        if (!rows.length) {
            tbody.innerHTML = `<tr><td colspan="11" style="text-align:center; color:#64748b; padding:28px;">No registered companies to file yet.</td></tr>`;
            return;
        }
        tbody.innerHTML = rows.map((row) => {
            const current = row.current || {};
            const readiness = row.readiness || {};
            const fileLabel = current.status === 'Filed' ? 'Update' : 'File';
            return `
                <tr>
                    <td>
                        <strong>${escapeHtml(row.name || 'Company')}</strong>
                        <div class="cell-subtext">${escapeHtml(row.company_number || '')}</div>
                    </td>
                    <td>${escapeHtml(row.owner_name || row.director || row.client_name || '—')}<div class="cell-subtext">${escapeHtml(row.director_email || row.client_email || '')}</div></td>
                    <td>${accountancyAddressBadge(readiness)}</td>
                    <td>${accountancyVerifyBadge(readiness.identity)}</td>
                    <td>${accountancyVerifyBadge(readiness.psc)}</td>
                    <td>${accountancyIssueBadge(readiness)}</td>
                    <td>${escapeHtml(formatUkNumericDate(current.period_end) || '—')}</td>
                    <td>${escapeHtml(formatUkNumericDate(current.due_date) || '—')}</td>
                    <td>${escapeHtml(current.accounts_type || 'Micro-entity')}</td>
                    <td><span class="status-badge ${accountancyStatusClass(current.status)}">${escapeHtml(current.status || 'Not started')}</span></td>
                    <td>
                        <div class="table-action-btns">
                            <button type="button" class="btn-secondary btn-table" data-accountancy-action="books" data-accountancy-id="${Number(row.id)}">Books</button>
                            <button type="button" class="btn-primary btn-table" data-accountancy-action="file" data-accountancy-id="${Number(row.id)}">${fileLabel}</button>
                        </div>
                    </td>
                </tr>
            `;
        }).join('');
    } catch (err) {
        if (errBox) {
            errBox.style.display = 'block';
            errBox.textContent = err.message || 'Unable to load accountancy.';
        }
        if (tbody) {
            tbody.innerHTML = `<tr><td colspan="11" style="text-align:center; color:#dc2626; padding:28px;">Unable to load accountancy.</td></tr>`;
        }
    }
}

async function loadClientAccountancy() {
    const tbody = document.getElementById('client-accountancy-table-body');
    const errBox = document.getElementById('client-accountancy-error');
    if (errBox) {
        errBox.style.display = 'none';
        errBox.textContent = '';
    }
    if (tbody) {
        tbody.innerHTML = `<tr><td colspan="8" style="text-align:center; color:#64748b; padding:28px;">Loading accounts…</td></tr>`;
    }
    try {
        const res = await fetch('/api/client/accountancy', { credentials: 'same-origin' });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data.status !== 'success') {
            throw new Error(data.message || 'Unable to load accountancy.');
        }
        const rows = Array.isArray(data.companies) ? data.companies : [];
        if (!tbody) return;
        if (!rows.length) {
            tbody.innerHTML = `<tr><td colspan="8" style="text-align:center; color:#64748b; padding:28px;">No registered companies yet.</td></tr>`;
            return;
        }
        tbody.innerHTML = rows.map((row) => {
            const current = row.current || {};
            const readiness = row.readiness || {};
            const canRequest = current.status !== 'Filed' && current.status !== 'Requested' && current.status !== 'In progress' && current.status !== 'Ready to file';
            return `
                <tr>
                    <td>
                        <strong>${escapeHtml(row.name || 'Company')}</strong>
                        <div class="cell-subtext">${escapeHtml(row.company_number || '')}</div>
                    </td>
                    <td>${accountancyAddressBadge(readiness)}</td>
                    <td>${accountancyVerifyBadge(readiness.identity)}</td>
                    <td>${accountancyVerifyBadge(readiness.psc)}</td>
                    <td>${escapeHtml(formatUkNumericDate(current.period_end) || '—')}</td>
                    <td>${escapeHtml(formatUkNumericDate(current.due_date) || '—')}</td>
                    <td><span class="status-badge ${accountancyStatusClass(current.status)}">${escapeHtml(current.status || 'Not started')}</span></td>
                    <td>${canRequest ? `<div class="table-action-btns"><button type="button" class="btn-primary btn-table" onclick="requestCompanyAccountsFiling(${Number(row.id)})">Request filing</button></div>` : ''}</td>
                </tr>
            `;
        }).join('');
    } catch (err) {
        if (errBox) {
            errBox.style.display = 'block';
            errBox.textContent = err.message || 'Unable to load accountancy.';
        }
        if (tbody) {
            tbody.innerHTML = `<tr><td colspan="8" style="text-align:center; color:#dc2626; padding:28px;">Unable to load accountancy.</td></tr>`;
        }
    }
}

async function loadAdminSettings() {
    try {
        let res = await fetch('/api/admin/settings', { credentials: 'same-origin' });
        let data = await res.json().catch(() => ({}));
        if (!res.ok || data.status !== 'success') {
            res = await fetch('/api/settings', { credentials: 'same-origin' });
            data = await res.json().catch(() => ({}));
        }
        if (data.status === 'success') {
            const s = data.settings || {};
            if (s.company_name) document.getElementById('set-company-name').value = s.company_name;
            if (s.support_email) document.getElementById('set-support-email').value = s.support_email;
            const teamEmails = document.getElementById('set-team-notification-emails');
            if (teamEmails) teamEmails.value = s.team_notification_emails || '';
            if (s.currency) document.getElementById('set-currency').value = s.currency;
            if (s.order_prefix) document.getElementById('set-order-prefix').value = s.order_prefix;
            const formfillUrl = document.getElementById('set-uk-formfill-url');
            if (formfillUrl) formfillUrl.value = s.uk_formfill_pro_url || DEFAULT_UK_FORMFILL_URL;
            if (s.uk_formfill_pro_url) ukFormfillUrlCache = String(s.uk_formfill_pro_url).replace(/\/$/, '');
            const chStatus = document.getElementById('set-companies-house-status');
            if (chStatus) {
                chStatus.textContent = s.companies_house_configured
                    ? 'Companies House is connected. Paste a new key only if you need to replace it.'
                    : 'Create a free key at developer.company-information.service.gov.uk, then paste it here.';
            }
            const smtpHost = document.getElementById('set-smtp-host');
            const smtpPort = document.getElementById('set-smtp-port');
            const smtpUser = document.getElementById('set-smtp-user');
            const smtpFrom = document.getElementById('set-smtp-from');
            const smtpStatus = document.getElementById('set-smtp-status');
            if (smtpHost && s.smtp_host) smtpHost.value = s.smtp_host;
            if (smtpPort && s.smtp_port) smtpPort.value = s.smtp_port;
            if (smtpUser && s.smtp_user) smtpUser.value = s.smtp_user;
            if (smtpFrom && s.smtp_from) smtpFrom.value = s.smtp_from;
            if (smtpStatus) {
                smtpStatus.textContent = s.smtp_configured
                    ? 'Customer emails are enabled. Uploaded documents will email the client automatically.'
                    : 'Add your Hostinger SMTP details below, save, then send a test email to confirm delivery.';
            }
            const ca = s.compliance_alerts || {};
            const setChk = (id, val) => { const el = document.getElementById(id); if (el) el.checked = !!val; };
            const setVal = (id, val) => { const el = document.getElementById(id); if (el && val != null && val !== '') el.value = val; };
            setChk('set-compliance-enabled', ca.enabled);
            setChk('set-compliance-verified-only', !!ca.verified_only);
            setChk('set-compliance-whatsapp', ca.whatsapp_enabled !== false);
            setChk('set-compliance-test-mode', ca.test_mode);
            setVal('set-compliance-hour', ca.send_hour != null ? ca.send_hour : 11);
            setVal('set-compliance-interval', ca.interval_hours != null ? ca.interval_hours : 24);
            setVal('set-compliance-timezone', ca.timezone || 'Asia/Karachi');
            setVal('set-compliance-test-recipient', ca.test_recipient || 'brixenconsultant@gmail.com');
            setVal('set-compliance-website', ca.website || s.compliance_alerts_website || 'https://brixenconsultants.com');
            setVal('set-compliance-whatsapp-url', ca.whatsapp_url || s.compliance_alerts_whatsapp_url || '');
        }
    } catch (err) { console.error(err); }
}

async function sendRmkComplianceTestEmail() {
    const statusEl = document.getElementById('set-compliance-test-status');
    if (statusEl) statusEl.textContent = 'Preparing RMK TRADING test…';
    try {
        const recipient = ((document.getElementById('set-compliance-test-recipient') || {}).value || '').trim();
        const res = await fetch('/api/admin/compliance-alerts/test-rmk', {
            method: 'POST',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ email: recipient || 'brixenconsultant@gmail.com' }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data.status !== 'success') {
            throw new Error(data.message || 'RMK test blocked.');
        }
        const companyName = (data.company && data.company.name) || 'RMK TRADING';
        if (statusEl) statusEl.textContent = `Test sent for ${companyName} → ${data.recipient || recipient}`;
    } catch (err) {
        if (statusEl) statusEl.textContent = err.message || 'RMK test failed.';
    }
}

async function sendAdminSettingsTestEmail() {
    const statusEl = document.getElementById('set-smtp-test-status');
    const btn = document.getElementById('set-smtp-test-btn');
    if (statusEl) statusEl.textContent = 'Sending test email…';
    if (btn) btn.disabled = true;
    try {
        const res = await fetch('/api/admin/settings/test-email', {
            method: 'POST',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ email: 'contact@brixenconsultants.com' }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data.status !== 'success') {
            throw new Error(data.message || 'Test email failed.');
        }
        if (statusEl) statusEl.textContent = data.message || 'Test email sent to contact@brixenconsultants.com.';
    } catch (err) {
        if (statusEl) statusEl.textContent = err.message || 'Test email failed.';
    } finally {
        if (btn) btn.disabled = false;
    }
}

async function saveAdminSettings(e) {
    e.preventDefault();
    const company_name = document.getElementById('set-company-name').value;
    const support_email = document.getElementById('set-support-email').value;
    const team_notification_emails = (document.getElementById('set-team-notification-emails') || {}).value || '';
    const currency = document.getElementById('set-currency').value;
    const order_prefix = document.getElementById('set-order-prefix').value;
    const companies_house_api_key = (document.getElementById('set-companies-house-api-key') || {}).value || '';
    const uk_formfill_pro_url = ((document.getElementById('set-uk-formfill-url') || {}).value || '').trim();
    const payload = { company_name, support_email, team_notification_emails, currency, order_prefix };
    if (companies_house_api_key.trim()) payload.companies_house_api_key = companies_house_api_key.trim();
    if (uk_formfill_pro_url) {
        payload.uk_formfill_pro_url = uk_formfill_pro_url.replace(/\/$/, '');
        ukFormfillUrlCache = payload.uk_formfill_pro_url;
    }
    const smtp_host = (document.getElementById('set-smtp-host') || {}).value || '';
    const smtp_port = (document.getElementById('set-smtp-port') || {}).value || '';
    const smtp_user = (document.getElementById('set-smtp-user') || {}).value || '';
    const smtp_pass = (document.getElementById('set-smtp-pass') || {}).value || '';
    const smtp_from = (document.getElementById('set-smtp-from') || {}).value || '';
    if (smtp_host.trim()) payload.smtp_host = smtp_host.trim();
    if (smtp_port.trim()) payload.smtp_port = smtp_port.trim();
    if (smtp_user.trim()) payload.smtp_user = smtp_user.trim();
    if (smtp_pass.trim()) payload.smtp_pass = smtp_pass.trim();
    if (smtp_from.trim()) payload.smtp_from = smtp_from.trim();
    payload.compliance_alerts_enabled = !!(document.getElementById('set-compliance-enabled') || {}).checked;
    payload.compliance_alerts_verified_only = !!(document.getElementById('set-compliance-verified-only') || {}).checked;
    payload.compliance_alerts_whatsapp_enabled = !!(document.getElementById('set-compliance-whatsapp') || {}).checked;
    payload.compliance_alerts_test_mode = !!(document.getElementById('set-compliance-test-mode') || {}).checked;
    payload.compliance_alerts_send_hour = ((document.getElementById('set-compliance-hour') || {}).value || '11').trim();
    payload.compliance_alerts_interval_hours = ((document.getElementById('set-compliance-interval') || {}).value || '24').trim();
    payload.compliance_alerts_timezone = ((document.getElementById('set-compliance-timezone') || {}).value || 'Asia/Karachi').trim();
    payload.compliance_alerts_test_recipient = ((document.getElementById('set-compliance-test-recipient') || {}).value || '').trim();
    payload.compliance_alerts_website = ((document.getElementById('set-compliance-website') || {}).value || '').trim();
    payload.compliance_alerts_whatsapp_url = ((document.getElementById('set-compliance-whatsapp-url') || {}).value || '').trim();

    try {
        const res = await fetch('/api/admin/settings', {
            method: 'POST',
            credentials: 'same-origin',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(payload)
        });
        const data = await res.json().catch(() => ({}));
        const keyField = document.getElementById('set-companies-house-api-key');
        if (keyField) keyField.value = '';
        const passField = document.getElementById('set-smtp-pass');
        if (passField) passField.value = '';
        alert(data.message || 'Admin settings saved! Branding updated.');
        document.querySelectorAll('.brand-name-display').forEach(el => el.textContent = company_name);
        loadAdminSettings();
    } catch (err) { console.error(err); }
}

// ----------------------------------------------------
// UTILITIES & NOTIFICATIONS
// ----------------------------------------------------
function isStaffTaskNotification(note) {
    const type = String(note && note.type ? note.type : '').toLowerCase();
    return type === 'task_assigned' || type === 'task_completed' || type.startsWith('task');
}

function visibleNotificationsForUser(notes) {
    const list = Array.isArray(notes) ? notes : [];
    if (currentUser && currentUser.role === 'CLIENT') {
        return list.filter(n => !isStaffTaskNotification(n));
    }
    return list;
}

function setUnreadBadge(count) {
    const badge = document.getElementById('header-unread-count');
    if (!badge) return;
    const n = Number(count) || 0;
    badge.textContent = n > 99 ? '99+' : String(n);
    badge.style.display = n > 0 ? 'flex' : 'none';
}

async function loadNotificationsCount() {
    try {
        const res = await fetch('/api/client/notifications');
        const data = await res.json();
        if (data.status === 'success') {
            const visible = visibleNotificationsForUser(data.notifications || []);
            const unread = visible.filter(n => !n.is_read).length;
            setUnreadBadge(unread);
        }
    } catch (err) { console.error(err); }
}

function closeNotificationsDropdown() {
    const dropdown = document.getElementById('notifications-dropdown');
    if (dropdown) dropdown.style.display = 'none';
    notificationsOpen = false;
}

async function toggleNotificationsModal(event) {
    if (event) event.stopPropagation();
    const dropdown = document.getElementById('notifications-dropdown');
    if (!dropdown) return;
    if (notificationsOpen) {
        closeNotificationsDropdown();
        return;
    }
    await openNotificationsDropdown();
}

async function openNotificationsDropdown() {
    const dropdown = document.getElementById('notifications-dropdown');
    const list = document.getElementById('notifications-list');
    if (!dropdown || !list) return;
    notificationsOpen = true;
    dropdown.style.display = 'block';
    list.innerHTML = `<div style="padding:20px 14px; text-align:center; color:#64748b; font-size:0.82rem;">Loading notifications...</div>`;
    try {
        const res = await fetch('/api/client/notifications');
        const data = await res.json();
        if (!res.ok || data.status !== 'success') {
            throw new Error(data.message || 'Unable to load notifications.');
        }
        lastNotifications = visibleNotificationsForUser(data.notifications || []);
        renderNotificationsList(lastNotifications);
        const unread = lastNotifications.filter(n => !n.is_read).length;
        setUnreadBadge(unread);
    } catch (err) {
        list.innerHTML = `<div style="padding:16px 14px; color:#dc2626; font-size:0.82rem; font-weight:600;">${escapeHtml(err.message || 'Unable to load notifications.')}</div>`;
    }
}

function renderNotificationsList(notes) {
    const list = document.getElementById('notifications-list');
    if (!list) return;
    if (!notes.length) {
        list.innerHTML = `<div style="padding:28px 14px; text-align:center; color:#64748b; font-size:0.82rem;">No notifications.</div>`;
        return;
    }
    list.innerHTML = notes.map((n, idx) => {
        const unread = !n.is_read;
        return `
            <button type="button" data-note-idx="${idx}" onclick="handleNotificationItemClick(${idx})" style="display:block; width:100%; text-align:left; border:0; border-bottom:1px solid #f1f5f9; background:${unread ? '#f8fafc' : '#ffffff'}; padding:12px 14px; cursor:pointer;">
                <div style="font-size:0.82rem; font-weight:700; color:#0f172a;">${escapeHtml(n.title || 'Notification')}</div>
                <div style="font-size:0.78rem; color:#64748b; margin-top:4px;">${escapeHtml(n.message || '')}</div>
                <div style="font-size:0.72rem; color:#94a3b8; margin-top:6px;">${escapeHtml(formatDateTime(n.created_at))}</div>
            </button>
        `;
    }).join('');
}

function resolveNotificationView(note) {
    if (!note) return null;
    if (isStaffTaskNotification(note)) {
        if (currentUser && currentUser.role === 'CLIENT') return null;
        return isAdminShellUser(currentUser) ? 'admin-tasks' : null;
    }
    const raw = String(note.link || '').replace(/^#/, '').replace(/^\//, '');
    if (isAdminShellUser(currentUser)) {
        if (raw === 'admin-tasks' || raw.includes('admin-tasks')) return 'admin-tasks';
        if (raw === 'orders' || raw === 'admin-orders') return 'admin-orders';
        if (raw === 'documents' || raw === 'admin-documents') return 'admin-documents';
        if (raw.startsWith('admin-')) return raw;
        return null;
    }
    if (raw === 'orders' || raw === 'client-orders') return 'client-orders';
    if (raw === 'documents' || raw === 'client-documents') return 'client-documents';
    if (raw === 'invoices' || raw === 'client-invoices') return 'client-invoices';
    if (raw === 'dashboard' || raw === 'client-dashboard') return 'client-dashboard';
    return null;
}

function handleNotificationItemClick(idx) {
    const note = lastNotifications[idx];
    closeNotificationsDropdown();
    const view = resolveNotificationView(note);
    if (view) switchView(view);
}

async function markNotificationsRead(event) {
    if (event) event.stopPropagation();
    try {
        const res = await fetch('/api/client/notifications/read', { method: 'POST' });
        const data = await res.json();
        if (!res.ok || data.status !== 'success') {
            throw new Error(data.message || 'Unable to mark notifications read.');
        }
        lastNotifications = lastNotifications.map(n => ({ ...n, is_read: 1 }));
        renderNotificationsList(lastNotifications);
        setUnreadBadge(0);
    } catch (err) {
        const list = document.getElementById('notifications-list');
        if (list) {
            list.insertAdjacentHTML('afterbegin', `<div style="padding:10px 14px; color:#dc2626; font-size:0.78rem; font-weight:600;">${escapeHtml(err.message)}</div>`);
        }
    }
}

function formatDob(value) {
    if (!value || String(value).trim() === '' || String(value).trim() === '—') return '—';
    const d = new Date(String(value).trim());
    if (Number.isNaN(d.getTime())) return String(value).trim();
    return d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' });
}

function formatUkPhone(phone) {
    if (!phone || String(phone).trim() === '' || String(phone).trim() === '—') return '—';
    const raw = String(phone).trim();
    const lower = raw.toLowerCase();
    if (/\.(jpe?g|png|pdf|gif|webp|docx?|zip|bmp|svg)\b/.test(lower)) return '—';
    if (/@|https?:\/\//.test(lower)) return '—';
    if (/(photo|image|upload|document|attachment|proof|passport|cnic|filename)/.test(lower)) return '—';
    let digits = raw.replace(/\D/g, '');
    if (!digits) return '—';
    let national = '';
    if (digits.startsWith('44') && digits.length >= 12) {
        national = digits.slice(2);
    } else if (digits.startsWith('0') && digits.length >= 11) {
        national = digits.slice(1);
    } else if (digits.length >= 10) {
        national = digits.slice(-10);
    } else {
        return '—';
    }
    if (national.length !== 10 || !'123789'.includes(national[0])) return '—';
    return `+44 ${national.slice(0, 4)} ${national.slice(4)}`;
}

function formatDateTime(dateStr) {
    if (!dateStr) return '';
    const d = new Date(dateStr);
    return d.toLocaleString('en-GB', { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' });
}

function formatDate(dateStr) {
    if (!dateStr) return '';
    const d = new Date(dateStr);
    if (Number.isNaN(d.getTime())) {
        const raw = String(dateStr).trim();
        return raw || '';
    }
    return d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' });
}

function formatShortDate(dateStr) {
    if (!dateStr) return '';
    const d = new Date(dateStr);
    if (Number.isNaN(d.getTime())) return '';
    const day = String(d.getDate()).padStart(2, '0');
    const month = String(d.getMonth() + 1).padStart(2, '0');
    const year = String(d.getFullYear()).slice(-2);
    return `${day}/${month}/${year}`;
}

let globalSearchTimer = null;
let globalSearchItems = [];
let globalSearchIndex = -1;

function handleGlobalSearch(query) {
    const q = String(query || '').trim();
    clearTimeout(globalSearchTimer);
    if (q.length < 2) {
        hideGlobalSearchResults();
        return;
    }
    showGlobalSearchMessage('Searching…');
    globalSearchTimer = setTimeout(() => fetchGlobalSearch(q), 120);
}

function hideGlobalSearchResults() {
    const panel = document.getElementById('global-search-results');
    if (panel) {
        panel.hidden = true;
        panel.innerHTML = '';
        panel.style.left = '';
        panel.style.top = '';
        panel.style.width = '';
    }
    globalSearchItems = [];
    globalSearchIndex = -1;
}

function positionGlobalSearchResults() {
    const wrap = document.getElementById('header-search-box');
    const panel = document.getElementById('global-search-results');
    if (!wrap || !panel || panel.hidden || wrap.style.display === 'none') return;
    const rect = wrap.getBoundingClientRect();
    const width = Math.max(320, Math.min(rect.width, window.innerWidth - 24));
    let left = rect.left;
    if (left + width > window.innerWidth - 12) left = Math.max(12, window.innerWidth - width - 12);
    panel.style.left = `${Math.round(left)}px`;
    panel.style.top = `${Math.round(rect.bottom + 8)}px`;
    panel.style.width = `${Math.round(width)}px`;
}

function showGlobalSearchMessage(message) {
    const panel = document.getElementById('global-search-results');
    if (!panel) return;
    panel.innerHTML = `<div class="global-search-empty">${escapeHtml(message)}</div>`;
    panel.hidden = false;
    positionGlobalSearchResults();
}

async function fetchGlobalSearch(query) {
    const panel = document.getElementById('global-search-results');
    if (!panel || !currentUser) return;
    try {
        const res = await fetch(`/api/search?q=${encodeURIComponent(query)}`, { credentials: 'same-origin' });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data.status !== 'success') {
            showGlobalSearchMessage('Unable to search right now.');
            return;
        }
        renderGlobalSearchResults(data.results || {});
    } catch (err) {
        showGlobalSearchMessage('Unable to search right now.');
    }
}

function flattenSearchResults(results) {
    const groups = [
        ['customers', 'Clients'],
        ['orders', 'Orders'],
        ['companies', 'Companies'],
        ['documents', 'Documents'],
        ['invoices', 'Invoices']
    ];
    const items = [];
    groups.forEach(([key]) => {
        (results[key] || []).forEach((row) => items.push(row));
    });
    return items;
}

function renderGlobalSearchResults(results) {
    const panel = document.getElementById('global-search-results');
    if (!panel) return;
    const groups = [
        ['customers', 'Clients'],
        ['orders', 'Orders'],
        ['companies', 'Companies'],
        ['documents', 'Documents'],
        ['invoices', 'Invoices']
    ];
    globalSearchItems = flattenSearchResults(results);
    globalSearchIndex = -1;
    if (!globalSearchItems.length) {
        showGlobalSearchMessage('No matching clients, orders, or companies.');
        return;
    }
    let html = '';
    groups.forEach(([key, label]) => {
        const rows = results[key] || [];
        if (!rows.length) return;
        html += `<div class="global-search-group"><div class="global-search-group-title">${label}</div>`;
        rows.forEach((row) => {
            const payload = encodeURIComponent(JSON.stringify(row));
            html += `<button type="button" class="global-search-item" data-search-item="${payload}">
                <span class="global-search-item-title">${escapeHtml(row.title || '')}</span>
                <span class="global-search-item-sub">${escapeHtml(row.subtitle || '')}</span>
            </button>`;
        });
        html += `</div>`;
    });
    panel.innerHTML = html;
    panel.hidden = false;
    positionGlobalSearchResults();
    panel.querySelectorAll('.global-search-item').forEach((btn) => {
        btn.addEventListener('mousedown', (event) => {
            event.preventDefault();
            try {
                openGlobalSearchResult(JSON.parse(decodeURIComponent(btn.getAttribute('data-search-item') || '')));
            } catch (err) { /* ignore */ }
        });
    });
}

function highlightGlobalSearchItem() {
    const panel = document.getElementById('global-search-results');
    if (!panel) return;
    panel.querySelectorAll('.global-search-item').forEach((btn, idx) => {
        btn.classList.toggle('is-active', idx === globalSearchIndex);
        if (idx === globalSearchIndex) btn.scrollIntoView({ block: 'nearest' });
    });
}

function openGlobalSearchResult(item) {
    if (!item || !item.id) return;
    hideGlobalSearchResults();
    const input = document.getElementById('global-search-input');
    if (input) input.blur();
    if (item.type === 'customer') {
        switchView('admin-customers');
        setTimeout(() => openCrmClientModal(item.id), 60);
        return;
    }
    if (item.type === 'order') {
        if (isAdminShellUser(currentUser)) {
            switchView('admin-orders');
            setTimeout(() => openStaffOrderWorkspace(item.id), 60);
        } else {
            switchView('client-orders');
            setTimeout(() => openOrderDetailsModal(item.id), 60);
        }
        return;
    }
    if (item.type === 'company') {
        switchView(isAdminShellUser(currentUser) ? 'admin-companies' : 'client-companies');
        setTimeout(() => openCompanyPortfolioDetail(item.id), 60);
        return;
    }
    if (item.type === 'document') {
        switchView(isAdminShellUser(currentUser) ? 'admin-documents' : 'client-documents');
        setTimeout(() => openDocumentPreview(item.id, item.title, item.file_type || ''), 60);
        return;
    }
    if (item.type === 'invoice') {
        switchView(isAdminShellUser(currentUser) ? 'admin-invoices' : 'client-invoices');
    }
}

document.addEventListener('keydown', (event) => {
    if ((event.metaKey || event.ctrlKey) && (event.key === 'k' || event.key === 'K')) {
        const input = document.getElementById('global-search-input');
        const box = document.getElementById('header-search-box');
        if (input && box && box.style.display !== 'none') {
            event.preventDefault();
            input.focus();
            input.select();
        }
        return;
    }
    const panel = document.getElementById('global-search-results');
    if (!panel || panel.hidden) return;
    if (event.key === 'Escape') {
        hideGlobalSearchResults();
        return;
    }
    if (event.key === 'ArrowDown') {
        event.preventDefault();
        globalSearchIndex = Math.min(globalSearchItems.length - 1, globalSearchIndex + 1);
        highlightGlobalSearchItem();
        return;
    }
    if (event.key === 'ArrowUp') {
        event.preventDefault();
        globalSearchIndex = Math.max(0, globalSearchIndex - 1);
        highlightGlobalSearchItem();
        return;
    }
    if (event.key === 'Enter' && globalSearchIndex >= 0 && globalSearchItems[globalSearchIndex]) {
        event.preventDefault();
        openGlobalSearchResult(globalSearchItems[globalSearchIndex]);
    }
});

document.addEventListener('click', (event) => {
    const wrap = document.getElementById('header-search-box');
    const panel = document.getElementById('global-search-results');
    if (wrap && wrap.contains(event.target)) return;
    if (panel && panel.contains(event.target)) return;
    hideGlobalSearchResults();
});

window.addEventListener('resize', positionGlobalSearchResults);
window.addEventListener('scroll', positionGlobalSearchResults, true);

let intakeQueueItems = [];
let intakeBatchIdentities = [];
let currentIntakeDocType = '';
let currentIntakeDocTypes = [];

const INTAKE_ALLOWED_EXTENSIONS = new Set([
    'pdf', 'png', 'jpg', 'jpeg', 'webp', 'tif', 'tiff',
]);
const INTAKE_DOC_TYPE_OPTIONS = [
    { value: '', label: 'Auto-detect' },
    { value: 'Passport', label: 'Passport' },
    { value: 'Driving Licence', label: 'Driving licence' },
    { value: 'Identity Card', label: 'Identity card' },
    { value: 'Bank Statement', label: 'Bank statement' },
    { value: 'Company Document', label: 'Company document' },
    { value: 'Invoice', label: 'Invoice' },
];
const INTAKE_ID_KINDS = new Set(['Passport', 'Identity Card', 'Driving Licence']);
const INTAKE_JUNK_BASENAMES = new Set([
    '.ds_store', 'thumbs.db', 'desktop.ini', '.localized', 'icon\r', 'ehthumbs.db',
]);
const INTAKE_JUNK_PATH_PARTS = [
    '__macosx', '/.git/', '/.svn/', '/node_modules/', '/.trash/', '/.tmp/',
];
const INTAKE_PASSPORT_RE = /passport/i;
const INTAKE_ID_CARD_RE = /(?:\bid\b[\s_-]?card|identity\s*card|national\s*id|cnic|nicop|\bnid\b|\bnic\b|id[\s_-]?verif)/i;
const INTAKE_LICENCE_RE = /driving\s*licen[cs]e|\bdvla\b|\blicen[cs]e\b/i;
const INTAKE_STATEMENT_RE = /bank\s*statement|\bstatement\b|account\s*statement|balance\s*statement/i;
const INTAKE_COMPANY_RE = /certificate\s*of\s*incorporat|incorporat|newinc|company[\s_-]?profile|confirmation\s*statement|psc0[0-9]|ap0[0-9]|tm0[0-9]|\baa_\b|memorandum|articles\s*of\s*association|companies\s*house|company\s*doc|share\s*cert|officer|director\s*list|subscriber/i;
const INTAKE_COMPANY_NUM_RE = /(?:^|[^0-9A-Za-z])((?:SC|NI|OC|SO)\d{6}|\d{8})(?=[_\-\s.]|$)/i;
const INTAKE_INVOICE_RE = /invoice|tax\s*invoice/i;
const INTAKE_RECEIPT_ONLY_RE = /\breceipt\b/i;

function intakeFileExtension(name) {
    const base = String(name || '').split(/[\\/]/).pop() || '';
    const parts = base.split('.');
    if (parts.length < 2) return '';
    return parts.pop().toLowerCase();
}

function intakeMimeExtension(file) {
    const mime = String((file && file.type) || '').toLowerCase();
    if (mime === 'image/jpeg' || mime === 'image/jpg') return 'jpg';
    if (mime === 'image/png') return 'png';
    if (mime === 'image/webp') return 'webp';
    if (mime === 'image/tiff') return 'tiff';
    if (mime === 'application/pdf') return 'pdf';
    return '';
}

function normalizeIntakeDocumentType(raw) {
    const text = String(raw || '').trim().toLowerCase();
    if (!text || text === 'auto' || text === 'auto-detect') return '';
    if (text === 'passport') return 'Passport';
    if (text === 'identity card' || text === 'id card' || text === 'id') return 'Identity Card';
    if (text === 'driving licence' || text === 'driving license' || text === 'licence' || text === 'license') {
        return 'Driving Licence';
    }
    if (text === 'bank statement' || text === 'statement') return 'Bank Statement';
    if (text === 'company document' || text === 'company documents' || text === 'company') {
        return 'Company Document';
    }
    if (text === 'invoice' || text === 'invoices') return 'Invoice';
    return '';
}

function classifyIntakeDocumentKind(pathLabel) {
    const text = String(pathLabel || '');
    const lower = text.toLowerCase().replace(/\\/g, '/');
    // Folder names count too: .../Passports/scan.jpg
    if (INTAKE_PASSPORT_RE.test(text) || /\/passports?\//.test(lower)) return 'Passport';
    if (INTAKE_ID_CARD_RE.test(text) || /\/(?:id[_-]?cards?|identity|cnic|ids?)\//.test(lower)) return 'Identity Card';
    if (INTAKE_LICENCE_RE.test(text) || /\/(?:licen[cs]es?|driving)\//.test(lower)) return 'Driving Licence';
    if (INTAKE_STATEMENT_RE.test(text) || /\/(?:bank[_-]?statements?|statements?)\//.test(lower)) return 'Bank Statement';
    if (INTAKE_COMPANY_RE.test(text) || INTAKE_COMPANY_NUM_RE.test(text) || /\/(?:company[_-]?docs?|companies[_-]?house|incorporation)\//.test(lower)) {
        return 'Company Document';
    }
    if (INTAKE_INVOICE_RE.test(text) || /\/invoices?\//.test(lower)) return 'Invoice';
    return null;
}

function getCheckedIntakeDocumentTypes() {
    // Prefer the visible group so empty/ready panels stay consistent.
    const groups = [
        document.getElementById('intake-type-checkboxes'),
        document.getElementById('intake-type-checkboxes-ready'),
    ].filter(Boolean);
    const source = groups.find((g) => g.offsetParent !== null) || groups[0];
    const boxes = source
        ? source.querySelectorAll('input[name="intake-doc-type"]:checked')
        : document.querySelectorAll('input[name="intake-doc-type"]:checked');
    const types = [];
    boxes.forEach((box) => {
        const normalized = normalizeIntakeDocumentType(box.value);
        if (normalized) {
            if (!types.includes(normalized)) types.push(normalized);
            if (normalized === 'Identity Card' || normalized === 'Passport') {
                if (!types.includes('Passport')) types.push('Passport');
                if (!types.includes('Identity Card')) types.push('Identity Card');
                if (!types.includes('Driving Licence')) types.push('Driving Licence');
            }
        }
    });
    return types;
}

function syncIntakeTypeCheckboxes(selectedTypes) {
    const wanted = new Set(
        (Array.isArray(selectedTypes) ? selectedTypes : [selectedTypes])
            .map((t) => normalizeIntakeDocumentType(t))
            .filter(Boolean)
    );
    document.querySelectorAll('input[name="intake-doc-type"]').forEach((box) => {
        const boxType = normalizeIntakeDocumentType(box.value);
        box.checked = wanted.has(boxType);
    });
}

function updateIntakeTypeHint() {
    const hint = document.getElementById('intake-type-hint');
    if (!hint) return;
    const types = Array.isArray(currentIntakeDocTypes) ? currentIntakeDocTypes : [];
    if (types.length) {
        hint.textContent = `Only ${types.join(', ')} will be queued. Other files are skipped.`;
    } else {
        hint.textContent = 'Select multiple. Leave all unticked to auto-detect.';
    }
}

function onIntakeTypeCheckboxChange() {
    let types = getCheckedIntakeDocumentTypes();
    if (currentIntakeSourceMode === 'ID_ONLY_DISCOVERY') {
        types = types.filter((t) => INTAKE_ID_KINDS.has(t));
    }
    currentIntakeDocTypes = types;
    currentIntakeDocType = types.length === 1 ? types[0] : '';
    syncIntakeTypeCheckboxes(types);
    updateIntakeTypeHint();
}

function setIntakeDocumentType(type) {
    const normalized = normalizeIntakeDocumentType(type);
    currentIntakeDocTypes = normalized ? [normalized] : [];
    currentIntakeDocType = normalized || '';
    syncIntakeTypeCheckboxes(currentIntakeDocTypes);
    updateIntakeTypeHint();
}

function startIntakeBrowse(kind) {
    if (kind === 'folder') {
        const folderInput = document.getElementById('intake-folder-input');
        if (folderInput) folderInput.click();
        return;
    }
    const fileInput = document.getElementById('intake-files-input');
    if (fileInput) fileInput.click();
}

function assessIntakeFileRelevance(file, relPath, mode, options) {
    const opts = options || {};
    const name = String((file && file.name) || '');
    const path = String(relPath || name || '');
    const lowerPath = path.toLowerCase().replace(/\\/g, '/');
    const base = name.split(/[\\/]/).pop() || name;
    const lowerBase = base.toLowerCase();
    let ext = intakeFileExtension(base);
    if (!ext || !INTAKE_ALLOWED_EXTENSIONS.has(ext)) {
        const mimeExt = intakeMimeExtension(file);
        if (mimeExt) ext = mimeExt;
    }
    const sourceMode = mode || currentIntakeSourceMode || 'CUSTOMER_UPLOADS';
    const selectedTypes = (Array.isArray(opts.allowedTypes) ? opts.allowedTypes : getCheckedIntakeDocumentTypes())
        .map((t) => normalizeIntakeDocumentType(t))
        .filter(Boolean);
    const fromFolder = Boolean(opts.fromFolder);
    const size = Number((file && file.size) || 0);

    if (!ext || !INTAKE_ALLOWED_EXTENSIONS.has(ext)) {
        return { ok: false, reason: `Unsupported type (.${ext || 'unknown'}) — use PDF, JPEG, or PNG` };
    }
    if (INTAKE_JUNK_BASENAMES.has(lowerBase)) {
        return { ok: false, reason: 'System/junk file' };
    }
    if (INTAKE_JUNK_PATH_PARTS.some((part) => lowerPath.includes(part))) {
        return { ok: false, reason: 'Ignored folder path' };
    }
    if (size > 0 && size < 800) {
        return { ok: false, reason: 'File too small / empty' };
    }
    if (size > 45 * 1024 * 1024) {
        return { ok: false, reason: 'File too large (max 45MB)' };
    }

    const kind = classifyIntakeDocumentKind(`${lowerPath} ${lowerBase}`);

    // Filter: when category boxes are ticked
    if (selectedTypes.length) {
        if (kind) {
            const matchesAllowed = selectedTypes.includes(kind) ||
                (INTAKE_ID_KINDS.has(kind) && selectedTypes.some((t) => INTAKE_ID_KINDS.has(t)));
            if (matchesAllowed) {
                if (sourceMode === 'ID_ONLY_DISCOVERY' && !INTAKE_ID_KINDS.has(kind)) {
                    return { ok: false, reason: 'ID-Only mode: passport / ID card / driving licence only' };
                }
                return { ok: true, reason: kind, document_type: kind, allowed_types: selectedTypes.slice() };
            } else {
                return { ok: false, reason: `Skipped (${kind}) — not in selected options` };
            }
        }
        // Generic camera photo or PDF (PHOTO-*.jpg, IMG_*.jpg, scan.pdf) with unclassified filename:
        // Accept into queue so high-quality OCR reads and confirms the document content!
        if (['pdf', 'png', 'jpg', 'jpeg', 'webp', 'tif', 'tiff'].includes(ext)) {
            const defaultDocType = (selectedTypes.length === 1) ? selectedTypes[0] : '';
            return { ok: true, reason: 'Scan queued — high-quality OCR will confirm type', document_type: defaultDocType, allowed_types: selectedTypes.slice() };
        }
        return {
            ok: false,
            reason: `Skipped — file format unsupported for selected options: ${selectedTypes.join(', ')}`,
        };
    }

    if (INTAKE_RECEIPT_ONLY_RE.test(`${lowerPath} ${lowerBase}`) && !INTAKE_INVOICE_RE.test(`${lowerPath} ${lowerBase}`)) {
        return { ok: false, reason: 'Receipts skipped — tick Invoice to include them' };
    }

    if (kind) {
        if (sourceMode === 'ID_ONLY_DISCOVERY' && !INTAKE_ID_KINDS.has(kind)) {
            return { ok: false, reason: 'ID-Only mode: passport / ID card / driving licence only' };
        }
        return { ok: true, reason: kind, document_type: kind };
    }

    if (['pdf', 'png', 'jpg', 'jpeg', 'webp', 'tif', 'tiff'].includes(ext)) {
        return { ok: true, reason: 'Scan queued — high-quality OCR will confirm type', document_type: '' };
    }
    return {
        ok: false,
        reason: 'Only passports, licences, ID cards, bank statements, company docs, or invoices',
    };
}

function getIntakeCategoryLabel(fname, forcedType) {
    const forced = normalizeIntakeDocumentType(forcedType);
    if (forced) return forced;
    return classifyIntakeDocumentKind(fname || '') || 'Auto-detect';
}

function intakeTypeSelectHtml(selected, idx) {
    const current = normalizeIntakeDocumentType(selected) || '';
    const opts = INTAKE_DOC_TYPE_OPTIONS.map((opt) => {
        const sel = (opt.value === current) ? ' selected' : '';
        return `<option value="${escapeHtml(opt.value)}"${sel}>${escapeHtml(opt.label)}</option>`;
    }).join('');
    return `<select class="intake-type-select" onchange="updateIntakeFileType(${idx}, this.value)" title="Document type">${opts}</select>`;
}

function updateIntakeFileType(index, value) {
    if (index < 0 || index >= intakeQueueItems.length) return;
    const item = intakeQueueItems[index];
    if (!item || item.upload_status === 'PROCESSING' || item.upload_status === 'PROCESSED') return;
    const normalized = normalizeIntakeDocumentType(value);
    item.document_type = normalized;
    item.category = normalized || getIntakeCategoryLabel(item.name || item.relPath || '');
    persistIntakeState();
}

function addFileToIntakeQueue(file, relPath, options) {
    if (!file) return { added: false, reason: 'Missing file' };
    const pathLabel = relPath || file.name;
    const selectedTypes = getCheckedIntakeDocumentTypes();
    currentIntakeDocTypes = selectedTypes.slice();
    currentIntakeDocType = selectedTypes.length === 1 ? selectedTypes[0] : '';
    const fromFolder = Boolean(options && options.fromFolder);
    const verdict = assessIntakeFileRelevance(file, pathLabel, currentIntakeSourceMode, {
        allowedTypes: selectedTypes,
        fromFolder,
    });
    if (!verdict.ok) {
        return { added: false, reason: verdict.reason, name: file.name };
    }
    const already = intakeQueueItems.some((item) => (
        item.name === file.name
        && Number(item.size) === Number(file.size)
        && (item.relPath || item.name) === pathLabel
    ));
    if (already) {
        return { added: false, reason: 'Duplicate in queue', name: file.name };
    }
    const docType = normalizeIntakeDocumentType(verdict.document_type) || '';
    intakeQueueItems.push({
        id: 'file_' + Date.now() + '_' + Math.random().toString(36).substr(2, 5),
        file,
        name: file.name,
        relPath: pathLabel,
        size: file.size,
        document_type: docType,
        allowed_types: Array.isArray(verdict.allowed_types) ? verdict.allowed_types : selectedTypes.slice(),
        category: docType || getIntakeCategoryLabel(file.name),
        upload_status: 'UPLOADED',
        process_status: 'Ready for processing',
        error: null,
    });
    return { added: true, name: file.name };
}

function enqueueIntakeFiles(fileList, getRelPath, options) {
    const files = Array.from(fileList || []);
    let added = 0;
    const skipped = [];
    const fromFolder = Boolean(options && options.fromFolder);
    files.forEach((file) => {
        const relPath = typeof getRelPath === 'function' ? getRelPath(file) : (file.webkitRelativePath || file.name);
        const result = addFileToIntakeQueue(file, relPath, { fromFolder });
        if (result.added) added += 1;
        else if (result.reason && result.reason !== 'Duplicate in queue') {
            skipped.push(`${result.name || file.name}: ${result.reason}`);
        }
    });
    renderIntakeFilePreview();
    showIntakeUploadSuccess(intakeQueueItems.length, skipped);
    return { added, skipped };
}

function intakeDocPriority(item) {
    const name = String((item && item.name) || '').toLowerCase();
    const cat = String((item && (item.document_type || item.category)) || '').toLowerCase();
    if (cat.includes('passport') || cat.includes('identity') || cat.includes('licence') || cat.includes('license')
        || name.includes('passport') || name.includes('cnic')) return 0;
    if (cat.includes('company') || name.includes('newinc') || name.includes('certificate')) return 1;
    if (cat.includes('bank') || name.includes('statement')) return 2;
    if (cat.includes('invoice')) return 3;
    return 4;
}

function sortIntakeQueueForProcessing(items) {
    return items
        .map((item, index) => ({ item, index }))
        .sort((a, b) => {
            const pa = intakeDocPriority(a.item);
            const pb = intakeDocPriority(b.item);
            if (pa !== pb) return pa - pb;
            return a.index - b.index;
        })
        .map((row) => row.item);
}

function updateIntakeDropZoneState() {
    const zone = document.getElementById('intake-drop-zone');
    const empty = document.getElementById('intake-drop-zone-empty');
    const ready = document.getElementById('intake-drop-zone-ready');
    const readyTitle = document.getElementById('intake-drop-zone-ready-title');
    const count = intakeQueueItems.length;
    if (!zone) return;
    zone.classList.toggle('is-ready', count > 0);
    zone.classList.remove('is-dragover');
    if (empty) empty.style.display = count > 0 ? 'none' : 'block';
    if (ready) ready.style.display = count > 0 ? 'block' : 'none';
    if (readyTitle) {
        readyTitle.textContent = count === 1
            ? '1 document queued — ready to process'
            : `${count} documents queued — ready to process`;
    }
    const badge = document.getElementById('intake-queue-status-badge');
    if (badge) {
        const processing = intakeQueueItems.some((i) => i.upload_status === 'PROCESSING');
        const failed = intakeQueueItems.filter((i) => i.upload_status === 'FAILED').length;
        const done = intakeQueueItems.filter((i) => i.upload_status === 'PROCESSED').length;
        if (processing) {
            badge.className = 'badge-status-pending';
            badge.textContent = 'Processing…';
        } else if (failed && done) {
            badge.className = 'badge-status-pending';
            badge.style.cssText = 'background:#fffbeb;color:#b45309;';
            badge.textContent = `${done} filed · ${failed} failed`;
        } else if (failed) {
            badge.className = 'badge-status-failed';
            badge.textContent = 'Processing failed';
        } else if (done && done === count) {
            badge.className = 'badge-status-approved';
            badge.textContent = '✓ All processed';
        } else {
            badge.className = 'badge-status-uploaded';
            badge.style.cssText = '';
            badge.textContent = '✓ Queued — click Process';
        }
    }
}

function showIntakeUploadSuccess(count, skipped) {
    const succBanner = document.getElementById('adm-intake-success');
    const errBanner = document.getElementById('adm-intake-error');
    const n = Number(count) || 0;
    const skippedList = Array.isArray(skipped) ? skipped : [];
    if (errBanner) {
        if (skippedList.length) {
            errBanner.style.display = 'block';
            const preview = skippedList.slice(0, 6).map((s) => escapeHtml(s)).join('<br>');
            const more = skippedList.length > 6 ? `<br>…and ${skippedList.length - 6} more` : '';
            errBanner.innerHTML = `Skipped ${skippedList.length} irrelevant/unsupported file(s):<br>${preview}${more}`;
        } else {
            errBanner.style.display = 'none';
            errBanner.textContent = '';
        }
    }
    if (!succBanner) return;
    if (n <= 0) {
        succBanner.style.display = skippedList.length ? 'none' : 'none';
        succBanner.textContent = '';
        return;
    }
    const label = n === 1
        ? '1 relevant document queued and ready to process.'
        : `${n} relevant documents queued and ready to process.`;
    succBanner.style.display = 'block';
    succBanner.innerHTML = `✓ ${label} Click Process — parallel fast upload (compressed images + binary transfer).`;
}

let currentIntakeSourceMode = 'CUSTOMER_UPLOADS';

function switchIntakeSourceMode(mode) {
    currentIntakeSourceMode = mode || 'CUSTOMER_UPLOADS';
    ['customer-uploads', 'import-folder', 'id-discovery'].forEach(m => {
        const btn = document.getElementById(`btn-source-${m}`);
        if (btn) btn.classList.remove('is-active');
    });
    const key = mode.toLowerCase().replace(/_/g, '-');
    const activeBtn = document.getElementById(`btn-source-${key}`);
    if (activeBtn) activeBtn.classList.add('is-active');

    const titleEl = document.getElementById('intake-zone-title');
    const descEl = document.getElementById('intake-zone-desc');
    if (mode === 'IMPORT_FOLDER') {
        if (titleEl) titleEl.textContent = 'Drag & Drop Folder / Directory Tree Here';
        if (descEl) descEl.textContent = 'Tick a document type below, then browse the folder. Images get high-quality OCR.';
    } else if (mode === 'ID_ONLY_DISCOVERY') {
        if (titleEl) titleEl.textContent = 'Drag & Drop Passport / ID / Driving Licence Here';
        if (descEl) descEl.textContent = 'ID mode: tick Passport, ID card, and/or Licence, then browse.';
        currentIntakeDocTypes = (currentIntakeDocTypes || []).filter((t) => INTAKE_ID_KINDS.has(t));
        currentIntakeDocType = currentIntakeDocTypes.length === 1 ? currentIntakeDocTypes[0] : '';
        syncIntakeTypeCheckboxes(currentIntakeDocTypes);
    } else {
        if (titleEl) titleEl.textContent = 'Drag & Drop Allowed Documents Here';
        if (descEl) descEl.textContent = 'Tick types in one line (multiple OK), then browse. Images get high-quality OCR.';
    }
    updateIntakeTypeHint();
}

function triggerIntakeFileInput() {
    startIntakeBrowse(currentIntakeSourceMode === 'IMPORT_FOLDER' ? 'folder' : 'files');
}

function onIntakeFolderSelected(event) {
    enqueueIntakeFiles(event.target.files || [], (f) => f.webkitRelativePath || f.name, { fromFolder: true });
    event.target.value = '';
}

async function compressIntakeFileForUpload(file) {
    if (!file) return file;
    const type = String(file.type || '').toLowerCase();
    const name = String(file.name || 'document');
    const isImage = type.startsWith('image/') || /\.(png|jpe?g|webp|tif|tiff)$/i.test(name);
    if (!isImage) return file;
    // Keep quality for OCR: only compress very large images; preserve resolution.
    if (file.size && file.size < 2.5 * 1024 * 1024) return file;
    try {
        const bitmap = await createImageBitmap(file);
        const maxEdge = 3200;
        const scale = Math.min(1, maxEdge / Math.max(bitmap.width, bitmap.height));
        // If already within OCR-friendly size, keep original bytes.
        if (scale >= 0.98 && file.size < 6 * 1024 * 1024) {
            if (typeof bitmap.close === 'function') bitmap.close();
            return file;
        }
        const w = Math.max(1, Math.round(bitmap.width * scale));
        const h = Math.max(1, Math.round(bitmap.height * scale));
        const canvas = document.createElement('canvas');
        canvas.width = w;
        canvas.height = h;
        const ctx = canvas.getContext('2d', { alpha: false });
        if (!ctx) return file;
        ctx.fillStyle = '#ffffff';
        ctx.fillRect(0, 0, w, h);
        ctx.imageSmoothingEnabled = true;
        ctx.imageSmoothingQuality = 'high';
        ctx.drawImage(bitmap, 0, 0, w, h);
        if (typeof bitmap.close === 'function') bitmap.close();
        const blob = await new Promise((resolve) => canvas.toBlob(resolve, 'image/jpeg', 0.92));
        if (!blob || blob.size >= file.size * 0.98) return file;
        const outName = name.replace(/\.(png|webp|tif|tiff|jpe?g)$/i, '') + '.jpg';
        return new File([blob], outName, { type: 'image/jpeg', lastModified: Date.now() });
    } catch (_) {
        return file;
    }
}

async function processOneIntakeFile(item, batchIdentities) {
    const sourceFile = item.file;
    if (!sourceFile) {
        throw new Error(`Could not read ${item.name || 'document'}.`);
    }
    const uploadFile = await compressIntakeFileForUpload(sourceFile);
    const form = new FormData();
    form.append('file', uploadFile, uploadFile.name || item.name || 'document.jpg');
    form.append('file_name', item.name || uploadFile.name || 'document.jpg');
    form.append('folder_path', item.relPath || item.name || uploadFile.name || '');
    form.append('source_mode', currentIntakeSourceMode || 'CUSTOMER_UPLOADS');
    form.append('document_type', normalizeIntakeDocumentType(item.document_type || item.category) || '');
    form.append('hq_ocr', '1');
    if (Array.isArray(item.allowed_types) && item.allowed_types.length) {
        form.append('allowed_types', JSON.stringify(item.allowed_types));
    }
    form.append('batch_identities', JSON.stringify(Array.isArray(batchIdentities) ? batchIdentities : []));

    const res = await fetch('/api/admin/documents/intake/process', {
        method: 'POST',
        credentials: 'same-origin',
        body: form,
    });
    const rawText = await res.text();
    let data = {};
    try {
        data = rawText ? JSON.parse(rawText) : {};
    } catch (_) {
        const isHtml504 = rawText && (rawText.includes('504') || rawText.includes('Gateway Time-out') || rawText.includes('nginx'));
        if (isHtml504) {
            throw new Error(`Server gateway timeout (504). Please retry this document.`);
        }
        const snippet = (rawText || '').replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 120);
        throw new Error(snippet ? `Server error (${res.status}): ${snippet}` : `Server error (${res.status}).`);
    }
    if (!res.ok || data.status !== 'success') {
        throw new Error(data.message || `Smart intake failed (${res.status}).`);
    }
    return data;
}

async function runIntakePool(items, concurrency, worker) {
    const list = Array.isArray(items) ? items : [];
    if (!list.length) return;
    const limit = Math.max(1, Math.min(Number(concurrency) || 1, list.length));
    let cursor = 0;
    async function runner() {
        while (cursor < list.length) {
            const index = cursor;
            cursor += 1;
            await worker(list[index], index);
        }
    }
    await Promise.all(Array.from({ length: limit }, () => runner()));
}

async function submitSmartIntakeForm(event) {
    if (event && typeof event.preventDefault === 'function') event.preventDefault();
    if (intakeQueueItems.length === 0) {
        const errBanner = document.getElementById('adm-intake-error');
        if (errBanner) {
            errBanner.style.display = 'block';
            errBanner.textContent = 'Upload at least one document before processing.';
        }
        return;
    }
    const submitBtn = document.getElementById('btn-intake-submit');
    const errBanner = document.getElementById('adm-intake-error');
    const succBanner = document.getElementById('adm-intake-success');
    if (errBanner) errBanner.style.display = 'none';
    if (succBanner) succBanner.style.display = 'none';

    if (submitBtn) {
        submitBtn.disabled = true;
        submitBtn.dataset.processing = 'true';
        submitBtn.innerHTML = `<i data-lucide="loader" class="spin"></i> Processing 1 of ${intakeQueueItems.length}…`;
        if (window.lucide && typeof window.lucide.createIcons === 'function') {
            window.lucide.createIcons();
        }
    }

    const aggregate = {
        status: 'success',
        message: '',
        file_results: [],
        filed_documents: [],
        client: null,
        company: null,
        missing_fields: [],
        scan: { ocr_used: false, methods: [] },
    };
    let okCount = 0;
    let failCount = 0;
    intakeBatchIdentities = Array.isArray(intakeBatchIdentities) ? intakeBatchIdentities : [];

    try {
        const ordered = sortIntakeQueueForProcessing(intakeQueueItems.slice());
        // Count already-processed rows toward success.
        ordered.forEach((item) => {
            if (item && item.upload_status === 'PROCESSED') okCount += 1;
        });
        const pending = ordered.filter((item) => item && item.upload_status !== 'PROCESSED');
        const idWave = pending.filter((item) => intakeDocPriority(item) === 0);
        const otherWave = pending.filter((item) => intakeDocPriority(item) !== 0);
        let completed = 0;

        const processItem = async (item) => {
            if (!item.file) {
                item.upload_status = 'FAILED';
                item.process_status = 'Missing file data — re-upload this file.';
                failCount += 1;
                renderIntakeFilePreview();
                return;
            }
            item.upload_status = 'PROCESSING';
            item.process_status = 'Compressing & uploading…';
            item.error = null;
            renderIntakeFilePreview();
            if (submitBtn) {
                submitBtn.innerHTML = `<i data-lucide="loader" class="spin"></i> Fast processing ${Math.min(completed + 1, pending.length)} / ${pending.length}…`;
                if (window.lucide && typeof window.lucide.createIcons === 'function') {
                    window.lucide.createIcons();
                }
            }
            try {
                const identitiesSnapshot = Array.isArray(intakeBatchIdentities) ? intakeBatchIdentities.slice() : [];
                const data = await processOneIntakeFile(item, identitiesSnapshot);
                if (Array.isArray(data.batch_identities) && data.batch_identities.length) {
                    const merged = Array.isArray(intakeBatchIdentities) ? intakeBatchIdentities.slice() : [];
                    data.batch_identities.forEach((ident) => {
                        if (!ident || !(ident.full_name || ident.client_id)) return;
                        const key = String(ident.client_id || ident.full_name || '').toLowerCase();
                        const idx = merged.findIndex((m) => String(m.client_id || m.full_name || '').toLowerCase() === key);
                        if (idx >= 0) merged[idx] = Object.assign({}, merged[idx], ident);
                        else merged.push(ident);
                    });
                    intakeBatchIdentities = merged;
                }
                const fr = (data.file_results && data.file_results[0]) || {};
                const filingStatus = String((fr.filing_status || {}).status || '');
                if (filingStatus === 'skipped') {
                    item.upload_status = 'FAILED';
                    item.process_status = (fr.filing_status || {}).message || 'Skipped — not a relevant document';
                    item.error = item.process_status;
                    failCount += 1;
                } else {
                    const ch = fr.companies_house_matching || {};
                    const linked = Boolean(
                        ch.linked_from_passport
                        || (fr.document_processing || {}).linked_passport_name
                        || ((ch.reasons || []).join(' ').toLowerCase().includes('same person'))
                    );
                    item.upload_status = 'PROCESSED';
                    item.process_status = linked
                        ? `Same person form · ${ch.company_name || ch.message || 'filed'}`
                        : (ch.company_name
                            ? `Filed · ${ch.company_name}${ch.confidence != null ? ` (${ch.confidence}%)` : ''}`
                            : (ch.message || data.message || 'Filed under client profile'));
                    item.result = data;
                    okCount += 1;
                    if (Array.isArray(data.file_results)) aggregate.file_results.push(...data.file_results);
                    if (Array.isArray(data.filed_documents)) aggregate.filed_documents.push(...data.filed_documents);
                    if (data.client) aggregate.client = data.client;
                    if (data.company) aggregate.company = data.company;
                    if (data.scan && data.scan.ocr_used) aggregate.scan.ocr_used = true;
                    if (data.scan && Array.isArray(data.scan.methods)) {
                        aggregate.scan.methods.push(...data.scan.methods);
                    }
                }
            } catch (fileErr) {
                failCount += 1;
                item.upload_status = 'FAILED';
                item.process_status = (fileErr && fileErr.message) || 'Processing failed';
                item.error = item.process_status;
            }
            completed += 1;
            renderIntakeFilePreview();
        };

        // IDs first (same-person linking), then remaining docs — both waves run in parallel.
        await runIntakePool(idWave, 4, processItem);
        await runIntakePool(otherWave, 6, processItem);

        aggregate.message = failCount
            ? `Processed ${okCount} of ${intakeQueueItems.length} document(s). ${failCount} failed — retry those rows or Reset and re-upload.`
            : `Smart Intake stored ${okCount} document(s) in Customer Uploads.`;

        if (okCount > 0) {
            if (succBanner) {
                succBanner.style.display = 'block';
                succBanner.innerHTML = `✓ ${escapeHtml(aggregate.message)}`;
            }
            renderIntakeResults(aggregate);
        }
        if (failCount > 0 && errBanner) {
            errBanner.style.display = 'block';
            errBanner.textContent = failCount === intakeQueueItems.length
                ? `All ${failCount} document(s) failed. ${intakeQueueItems[0] && intakeQueueItems[0].process_status ? intakeQueueItems[0].process_status : ''}`.trim()
                : `${failCount} document(s) failed. Successful files stay marked Processed — click Process again to retry failures only.`;
        }
    } catch (err) {
        console.error('Smart Intake process failed', err);
        if (errBanner) {
            errBanner.style.display = 'block';
            errBanner.textContent = (err && err.message) || 'Smart intake processing failed.';
        }
    } finally {
        if (submitBtn) {
            delete submitBtn.dataset.processing;
            submitBtn.disabled = intakeQueueItems.length === 0;
            const pending = intakeQueueItems.filter((i) => i.upload_status !== 'PROCESSED').length;
            const count = intakeQueueItems.length;
            if (pending > 0 && pending < count) {
                submitBtn.innerHTML = `<i data-lucide="cpu"></i> Retry ${pending} Failed Document${pending > 1 ? 's' : ''}`;
            } else {
                submitBtn.innerHTML = `<i data-lucide="cpu"></i> Process ${count > 0 ? count + ' Document' + (count > 1 ? 's' : '') : '& Auto-File Documents'}`;
            }
        }
        if (window.lucide && typeof window.lucide.createIcons === 'function') {
            window.lucide.createIcons();
        }
    }
}

function onIntakeFilesSelected(event) {
    enqueueIntakeFiles(event.target.files || [], (f) => f.name, { fromFolder: false });
    event.target.value = '';
}

function onIntakeDragOver(event) {
    event.preventDefault();
    event.stopPropagation();
    const zone = document.getElementById('intake-drop-zone');
    if (zone) zone.classList.add('is-dragover');
}

function onIntakeDragLeave(event) {
    event.preventDefault();
    event.stopPropagation();
    const zone = document.getElementById('intake-drop-zone');
    if (zone) zone.classList.remove('is-dragover');
}

function onIntakeDrop(event) {
    event.preventDefault();
    event.stopPropagation();
    onIntakeDragLeave(event);
    const dt = event.dataTransfer;
    if (dt && dt.files && dt.files.length > 0) {
        const files = Array.from(dt.files);
        const fromFolder = files.some((f) => String(f.webkitRelativePath || '').includes('/'));
        enqueueIntakeFiles(files, (f) => f.webkitRelativePath || f.name, { fromFolder });
    }
}

function renderIntakeFilePreview() {
    const container = document.getElementById('intake-file-preview-container');
    const tbody = document.getElementById('intake-file-preview-list');
    const submitBtn = document.getElementById('btn-intake-submit');
    const countEl = document.getElementById('intake-file-count');

    updateIntakeDropZoneState();

    if (!container || !tbody) return;

    if (intakeQueueItems.length === 0) {
        container.style.display = 'none';
        tbody.innerHTML = '';
        if (submitBtn) {
            submitBtn.disabled = true;
            submitBtn.innerHTML = `<i data-lucide="cpu"></i> Process &amp; Auto-File Documents`;
        }
        return;
    }

    container.style.display = 'block';
    if (countEl) countEl.textContent = String(intakeQueueItems.length);

    const count = intakeQueueItems.length;
    if (submitBtn && !submitBtn.dataset.processing) {
        submitBtn.disabled = false;
        submitBtn.innerHTML = `<i data-lucide="cpu"></i> Process ${count} Document${count > 1 ? 's' : ''}`;
    }

    try {
        tbody.innerHTML = intakeQueueItems.map((item, idx) => {
            let statusBadge = '';
            if (item.upload_status === 'UPLOADING') {
                statusBadge = `<span class="badge-status-pending" style="background:#fef3c7; color:#d97706;">Uploading...</span>`;
            } else if (item.upload_status === 'PROCESSING') {
                statusBadge = `<span class="badge-status-pending" style="background:#e0f2fe; color:#0284c7;">Processing OCR...</span>`;
            } else if (item.upload_status === 'PROCESSED') {
                statusBadge = `<span class="badge-status-approved">✓ Processed &amp; Filed</span>`;
            } else if (item.upload_status === 'FAILED') {
                statusBadge = `<span class="badge-status-failed">⚠ Failed</span>`;
            } else {
                statusBadge = `<span class="badge-status-uploaded">✓ Uploaded</span>`;
            }

            return `
            <tr>
                <td><strong>${escapeHtml(item.name)}</strong></td>
                <td>${(item.upload_status === 'PROCESSING' || item.upload_status === 'PROCESSED')
                    ? `<span class="badge-status-completed">${escapeHtml(item.document_type || item.category || 'Document')}</span>`
                    : intakeTypeSelectHtml(item.document_type || item.category, idx)}</td>
                <td>${escapeHtml(formatDocumentSize(item.size))}</td>
                <td>${statusBadge}</td>
                <td><span style="font-size:0.85rem; color:#475569;">${escapeHtml(item.process_status || '')}</span></td>
                <td>
                    <button type="button" onclick="removeIntakeFile(${idx})" class="btn-action" style="color:#ef4444; border-color:#fca5a5;" title="Remove file">
                        Remove
                    </button>
                </td>
            </tr>`;
        }).join('');
    } catch (err) {
        console.error('Smart Intake queue render failed', err);
        tbody.innerHTML = `<tr><td colspan="6" style="color:#b91c1c; padding:12px;">Could not render the uploaded file list. ${escapeHtml(err && err.message ? err.message : 'Unknown error')}</td></tr>`;
    }

    if (window.lucide && typeof window.lucide.createIcons === 'function') {
        window.lucide.createIcons();
    }
    persistIntakeState();
}

function removeIntakeFile(index) {
    if (index >= 0 && index < intakeQueueItems.length) {
        intakeQueueItems.splice(index, 1);
        renderIntakeFilePreview();
        showIntakeUploadSuccess(intakeQueueItems.length);
    }
}

function loadSmartIntakeView() {
    if (intakeQueueItems && intakeQueueItems.length > 0) {
        renderIntakeFilePreview();
        return;
    }
    try {
        const stored = sessionStorage.getItem('brixen_active_intake_queue');
        if (stored) {
            const parsed = JSON.parse(stored);
            if (Array.isArray(parsed) && parsed.length > 0) {
                intakeQueueItems = parsed;
                renderIntakeFilePreview();
                return;
            }
        }
    } catch (_) {}
    renderIntakeFilePreview();
}

function persistIntakeState() {
    try {
        if (intakeQueueItems && intakeQueueItems.length > 0) {
            const serializable = intakeQueueItems.map(i => ({
                id: i.id,
                name: i.name,
                size: i.size,
                category: i.category,
                document_type: i.document_type || '',
                upload_status: i.upload_status,
                process_status: i.process_status,
                error: i.error
            }));
            sessionStorage.setItem('brixen_active_intake_queue', JSON.stringify(serializable));
        } else {
            sessionStorage.removeItem('brixen_active_intake_queue');
        }
    } catch (_) {}
}

function resetSmartIntakeForm() {
    intakeQueueItems = [];
    intakeBatchIdentities = [];
    currentIntakeDocType = '';
    currentIntakeDocTypes = [];
    setIntakeDocumentType('');
    sessionStorage.removeItem('brixen_active_intake_queue');
    const inputFiles = document.getElementById('intake-files-input');
    if (inputFiles) inputFiles.value = '';
    const inputFolder = document.getElementById('intake-folder-input');
    if (inputFolder) inputFolder.value = '';
    renderIntakeFilePreview();
    const resultsPanel = document.getElementById('intake-results-panel');
    if (resultsPanel) resultsPanel.style.display = 'none';
    const errBanner = document.getElementById('adm-intake-error');
    if (errBanner) errBanner.style.display = 'none';
    const succBanner = document.getElementById('adm-intake-success');
    if (succBanner) succBanner.style.display = 'none';
}

function renderIntakeResults(data) {
    const resultsPanel = document.getElementById('intake-results-panel');
    if (!resultsPanel) return;
    resultsPanel.style.display = 'block';

    const client = data.client || {};
    const company = data.company || {};
    const missing = data.missing_fields || [];
    const filedDocs = data.filed_documents || [];
    const fileResults = data.file_results || [];

    const firstResult = fileResults[0] || {};
    const docProc = firstResult.document_processing || {};
    const chMatch = firstResult.companies_house_matching || {};

    // Client Profile Card — identity triad
    const extractedName = docProc.extracted_name || client.full_name || 'Unassigned / Anonymous';
    const extractedDob = docProc.extracted_dob || (client.dob && client.dob !== 'Not detected' ? client.dob : null);
    const extractedNat = docProc.nationality || (client.nationality && client.nationality !== 'Not detected' ? client.nationality : null);
    document.getElementById('res-client-name').textContent = extractedName;
    document.getElementById('res-client-dob').textContent = extractedDob ? `DOB: ${extractedDob}` : 'DOB: Not detected';
    const natEl = document.getElementById('res-client-nationality');
    if (natEl) natEl.textContent = extractedNat ? `Nationality: ${extractedNat}` : 'Nationality: Not detected';

    const clientMeta = document.getElementById('res-client-meta');
    if (clientMeta) {
        const metaParts = [];
        if (docProc.doc_detected) metaParts.push(`Doc: ${docProc.doc_detected}`);
        clientMeta.textContent = metaParts.join(' | ');
    }

    const clientBadge = document.getElementById('res-client-status-badge');
    if (clientBadge) {
        clientBadge.innerHTML = client.id ? `<span class="badge-status-approved">✓ Linked Client #${client.id}</span>` : `<span class="badge-status-pending">No Client Created</span>`;
    }
    const clientAction = document.getElementById('res-client-action-btn');
    if (clientAction && client.id) {
        clientAction.innerHTML = `<button type="button" class="btn-secondary" style="font-size:0.8rem;" onclick="openCrmClientModal(${client.id})"><i data-lucide="user"></i> View Customer File</button>`;
    }

    // Document scan accuracy card — name / DOB / nationality only
    const quality = docProc.extraction_quality || firstResult.extraction_quality || {};
    const accuracyScore = document.getElementById('res-accuracy-score');
    const accuracyFields = document.getElementById('res-accuracy-fields');
    const accuracyPreview = document.getElementById('res-accuracy-preview');
    const accuracyBadge = document.getElementById('res-accuracy-badge');
    const scoreVal = Number.isFinite(Number(quality.score)) ? Number(quality.score) : null;
    const scoreLabel = quality.label || (scoreVal == null ? 'Unknown' : (scoreVal >= 67 ? 'High' : (scoreVal >= 34 ? 'Medium' : 'Low')));
    if (accuracyScore) {
        accuracyScore.textContent = scoreVal == null ? 'Accuracy unavailable' : `${scoreLabel} · ${scoreVal}%`;
    }
    if (accuracyFields) {
        const idFields = quality.identity_fields || {};
        const parts = [
            (idFields.name || (extractedName && extractedName !== 'Unassigned / Anonymous')) ? 'Name ✓' : 'Name ✗',
            (idFields.dob || extractedDob) ? 'DOB ✓' : 'DOB ✗',
            (idFields.nationality || extractedNat) ? 'Nationality ✓' : 'Nationality ✗',
        ];
        const found = quality.fields_found;
        const checked = quality.fields_checked;
        accuracyFields.textContent = (found != null && checked != null)
            ? `Identity fields: ${found}/${checked} — ${parts.join(' · ')}`
            : `Identity fields: ${parts.join(' · ')}`;
    }
    if (accuracyPreview) {
        const preview = (docProc.text_preview || firstResult.text_preview || '').trim();
        accuracyPreview.textContent = preview
            ? `OCR preview: ${preview.slice(0, 160)}${preview.length > 160 ? '…' : ''}`
            : (quality.ocr_used ? 'OCR ran but name / DOB / nationality were not recovered.' : 'No OCR text available for this file.');
    }
    if (accuracyBadge) {
        let badgeStyle = 'background:#ecfdf5; color:#047857; border:1px solid #a7f3d0;';
        if (scoreVal != null && scoreVal < 34) badgeStyle = 'background:#fef2f2; color:#b91c1c; border:1px solid #fecaca;';
        else if (scoreVal != null && scoreVal < 67) badgeStyle = 'background:#fffbeb; color:#b45309; border:1px solid #fde68a;';
        accuracyBadge.innerHTML = `<span class="badge-status-pending" style="${badgeStyle}">${escapeHtml(scoreLabel)} identity confidence</span>`;
    }

    // Companies House Auto-Match Card
    const resCompCard = document.getElementById('res-company-card');
    const resCompName = document.getElementById('res-company-name');
    const resCompNum = document.getElementById('res-company-num');
    const resCompBadge = document.getElementById('res-company-status-badge');
    const resCompExpl = document.getElementById('res-company-explanation');

    const chStatus = chMatch.status || company.ch_status || (company.id ? 'matched' : 'no_ch_match');
    const isPassportDoc = fileResults.some(f => f.doc_type === 'Passport' || f.doc_type === 'ID Document' || (f.document_processing && (f.document_processing.doc_detected === 'Passport' || f.document_processing.doc_detected === 'ID Document')));
    const candidates = chMatch.top_candidates || chMatch.candidates || company.top_candidates || [];
    const candidatesEl = document.getElementById('res-company-candidates');
    if (candidatesEl) candidatesEl.innerHTML = '';
    const confidence = chMatch.confidence != null ? chMatch.confidence : company.confidence;
    const confidenceLabel = chMatch.confidence_label || company.confidence_label || '';
    const reasons = chMatch.reasons || company.match_reasons || [];

    function renderMatchReasons(list) {
        if (!list || !list.length) return '';
        return `<ul style="margin:8px 0 0; padding-left:18px; color:#334155; font-size:0.8rem; line-height:1.4;">${list.map(r => `<li>${escapeHtml(r)}</li>`).join('')}</ul>`;
    }

    function renderTopCandidates(list) {
        if (!candidatesEl || !list || !list.length) return;
        candidatesEl.innerHTML = `<div style="margin-top:12px; padding:10px 12px; background:var(--color-surface-soft, #f8fafc); border:1px solid var(--border-color, #e2e8f0); border-radius:8px;">
            <div style="font-size:0.8rem; font-weight:700; color:var(--text-primary, #0f172a); margin-bottom:8px; display:flex; align-items:center; gap:6px;">
                <i data-lucide="building-2" style="width:14px; height:14px; color:var(--color-primary, #2563eb);"></i> Companies House Suggested Matches for Staff Review:
            </div>
            ${list.slice(0, 4).map((c, idx) => {
                const score = c.score != null ? `${c.score}%` : '';
                const label = escapeHtml(c.name || 'Candidate');
                const num = escapeHtml(c.company_number || '—');
                const dob = c.officer_dob || {};
                let dobTxt = '';
                if (dob.year) {
                    dobTxt = dob.month
                        ? ` · CH birth ${String(dob.month).padStart(2, '0')}/${dob.year}`
                        : ` · CH birth year ${dob.year}`;
                }
                const why = (c.reasons || []).some(r => String(r).includes('Incompatible DOB'))
                    ? ' <span style="color:#b45309; font-weight:600;">(DOB mismatch)</span>'
                    : '';
                const compNumStr = String(c.company_number || '');
                return `
                    <div style="padding:8px 0; border-top:${idx > 0 ? '1px solid var(--border-color, #e2e8f0)' : 'none'}; display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:8px;">
                        <div>
                            <div style="font-weight:700; font-size:0.85rem; color:var(--text-primary, #0f172a);">${label}</div>
                            <div style="font-size:0.75rem; color:var(--text-secondary, #475569);">
                                Score: <strong>${escapeHtml(String(score))}</strong> · Company #${num}${escapeHtml(dobTxt)}${why}
                            </div>
                        </div>
                        ${compNumStr ? `
                            <button type="button" class="btn-secondary" style="padding:4px 10px; font-size:0.75rem;" onclick="assignIntakeCompanyCandidate('${escapeJsString(compNumStr)}', '${escapeJsString(c.name || '')}', '${escapeJsString(c.company_id || '')}')">
                                <i data-lucide="check" style="width:12px; height:12px;"></i> Select Company
                            </button>
                        ` : ''}
                    </div>
                `;
            }).join('')}
        </div>`;
        if (window.lucide && typeof window.lucide.createIcons === 'function') window.lucide.createIcons();
    }

    if (chStatus === 'matched') {
        if (resCompCard) resCompCard.style.borderTopColor = '#16a34a';
        if (resCompName) resCompName.textContent = (company.name || chMatch.company_name || 'Matched Entity');
        if (resCompNum) {
            const confVal = confidence != null ? confidence : 85;
            resCompNum.textContent = (company.company_number || chMatch.company_number)
                ? `Company #: ${company.company_number || chMatch.company_number} · Confidence: ${confVal}%`
                : `Confidence: ${confVal}%`;
        }
        if (resCompBadge) resCompBadge.innerHTML = `<span class="badge-status-approved">✓ HIGH-CONFIDENCE MATCH</span>`;
        if (resCompExpl) {
            resCompExpl.innerHTML = `<strong>Company:</strong> ${escapeHtml(company.name || chMatch.company_name || 'Matched Entity')}<br>` +
                `<strong>Confidence:</strong> ${confidence != null ? confidence : 85}%<br>` +
                `<strong>Evidence Checklist:</strong>${renderMatchReasons(reasons)}`;
        }
    } else if (chStatus === 'review_required' || chStatus === 'multiple_matches') {
        if (resCompCard) resCompCard.style.borderTopColor = '#eab308';
        if (resCompName) resCompName.textContent = '⚠ REVIEW REQUIRED';
        if (resCompNum) resCompNum.textContent = confidence != null ? `Top candidate confidence: ${confidence}%` : 'Ambiguous candidate evidence';
        if (resCompBadge) resCompBadge.innerHTML = `<span class="badge-status-pending" style="background:#fef3c7; color:#b45309;">⚠ REVIEW REQUIRED</span>`;
        if (resCompExpl) resCompExpl.textContent = company.ch_message || chMatch.message || 'Insufficient evidence to safely distinguish candidates. Please select manually.';
        renderTopCandidates(candidates);
    } else if (chStatus === 'not_applicable' || (isPassportDoc && chStatus !== 'matched')) {
        if (resCompCard) resCompCard.style.borderTopColor = '#0284c7';
        const hasCandidates = candidates && candidates.length;
        const dobBlocked = (reasons || []).some(r => String(r).includes('Incompatible DOB'))
            || (candidates || []).some(c => (c.reasons || []).some(r => String(r).includes('Incompatible DOB')));
        if (resCompName) {
            resCompName.textContent = dobBlocked
                ? 'No safe company match (DOB conflict)'
                : (hasCandidates ? 'No high-confidence company match' : 'No Companies House Match');
        }
        if (resCompNum) {
            resCompNum.textContent = confidence != null
                ? `Best score ${confidence}% · filed under client`
                : 'Identity document filed under client';
        }
        if (resCompBadge) {
            resCompBadge.innerHTML = dobBlocked
                ? `<span class="badge-status-pending" style="background:#fef3c7; color:#b45309; border:1px solid #fde68a;">✓ Client filed · CH officers are different people</span>`
                : `<span class="badge-status-pending" style="background:#e0f2fe; color:#0369a1; border:1px solid #bae6fd;">✓ Document Processed (No CH Link)</span>`;
        }
        if (resCompExpl) {
            resCompExpl.textContent = company.ch_message || chMatch.message
                || (isPassportDoc
                    ? 'Passports identify a person. Company stays unassigned until a high-confidence match exists.'
                    : 'No matching company entity found on Companies House. Document processing succeeded.');
        }
        if (hasCandidates) renderTopCandidates(candidates);
    } else {
        if (resCompCard) resCompCard.style.borderTopColor = '#0284c7';
        if (resCompName) resCompName.textContent = 'No Companies House Match';
        if (resCompNum) resCompNum.textContent = confidence != null ? `Best score ${confidence}%` : 'Filed under client';
        if (resCompBadge) resCompBadge.innerHTML = `<span class="badge-status-pending" style="background:#e0f2fe; color:#0369a1; border:1px solid #bae6fd;">✓ Document Processed (No CH Link)</span>`;
        if (resCompExpl) resCompExpl.textContent = chMatch.message || company.ch_message || 'No matching company entity found on Companies House.';
        if (candidates && candidates.length) renderTopCandidates(candidates);
    }

    // Pending Alert Card — informational only (automation already finished)
    const pendingText = document.getElementById('res-pending-alert-text');
    const pendingBtn = document.getElementById('res-pending-action-btn');
    if (missing && missing.length > 0) {
        if (pendingText) pendingText.textContent = `Optional later: ${missing.join(', ')}. Filing already completed — you can add these in the customer profile when you have them.`;
        if (pendingBtn && client.id) {
            pendingBtn.innerHTML = `<button type="button" class="btn-primary" style="font-size:0.8rem; background:#d97706;" onclick="openCrmClientModal(${client.id})"><i data-lucide="edit-3"></i> Add Details Later</button>`;
        }
    } else {
        if (pendingText) pendingText.textContent = `✓ Client contact details are complete. No action required.`;
        if (pendingBtn) pendingBtn.innerHTML = '';
    }

    // Filed docs table
    const tbody = document.getElementById('intake-filed-docs-tbody');
    if (tbody) {
        tbody.innerHTML = filedDocs.map((doc, idx) => {
            const fRes = fileResults[idx] || {};
            const docType = fRes.doc_type || doc.category || 'General';
            return `
                <tr>
                    <td><strong>${escapeHtml(doc.name || 'Document')}</strong></td>
                    <td><span class="badge-status-completed">${escapeHtml(docType)}</span></td>
                    <td>${escapeHtml(client.full_name || 'Client')}</td>
                    <td>${escapeHtml(company.name || 'Unassigned')}</td>
                    <td><span class="badge-status-approved">${escapeHtml((doc.scan_method || 'scan') === 'ocr' ? 'OCR scan' : 'Text scan')}</span></td>
                    <td><span class="badge-status-approved">Filed</span></td>
                    <td>
                        <a href="/api/documents/${doc.id}/view" target="_blank" class="btn-action"><i data-lucide="eye"></i> View</a>
                        <a href="/api/documents/${doc.id}/download" class="btn-action"><i data-lucide="download"></i> Download</a>
                    </td>
                </tr>
            `;
        }).join('');
    }

    if (window.lucide && typeof window.lucide.createIcons === 'function') {
        window.lucide.createIcons();
    }
}

async function linkIntakeCompaniesHouseCandidate(btn) {
    if (!btn || btn.dataset.linking === '1') return;
    const companyNumber = (btn.getAttribute('data-intake-link-company') || '').trim();
    const companyIdRaw = (btn.getAttribute('data-intake-link-company-id') || '').trim();
    const companyName = (btn.getAttribute('data-intake-link-name') || '').trim();
    const clientId = (btn.getAttribute('data-intake-link-client') || '').trim();
    const docIds = (btn.getAttribute('data-intake-link-docs') || '')
        .split(',')
        .map(s => s.trim())
        .filter(Boolean);
    if (!clientId || (!companyNumber && !companyIdRaw && !companyName)) {
        alert('Missing company details for linking.');
        return;
    }
    btn.dataset.linking = '1';
    const original = btn.innerHTML;
    btn.disabled = true;
    btn.textContent = 'Linking…';
    try {
        const payload = {
            client_id: Number(clientId),
            company_name: companyName || null,
            document_ids: docIds,
        };
        if (companyIdRaw) payload.company_id = Number(companyIdRaw);
        if (companyNumber) payload.company_number = companyNumber;
        const res = await fetch('/api/admin/documents/intake/link-company', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'same-origin',
            body: JSON.stringify(payload),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data.status !== 'success') {
            throw new Error(data.message || 'Could not link company');
        }
        const resCompName = document.getElementById('res-company-name');
        const resCompNum = document.getElementById('res-company-num');
        const resCompBadge = document.getElementById('res-company-status-badge');
        const resCompExpl = document.getElementById('res-company-explanation');
        const resCompCard = document.getElementById('res-company-card');
        const candidatesEl = document.getElementById('res-company-candidates');
        if (resCompCard) resCompCard.style.borderTopColor = '#16a34a';
        if (resCompName) resCompName.textContent = (data.company && data.company.name) || companyName || 'Matched Entity';
        if (resCompNum) resCompNum.textContent = `Company #: ${(data.company && data.company.company_number) || companyNumber || companyIdRaw || 'Linked'}`;
        if (resCompBadge) resCompBadge.innerHTML = `<span class="badge-status-approved">✓ Companies House Match Linked</span>`;
        if (resCompExpl) resCompExpl.textContent = data.message || 'Linked company to client and auto-filed documents.';
        if (candidatesEl) candidatesEl.innerHTML = '';
    } catch (err) {
        alert((err && err.message) || 'Could not link company');
        btn.disabled = false;
        btn.innerHTML = original;
        delete btn.dataset.linking;
    }
}

// ----------------------------------------------------
// STAFF DASHBOARD, TEAM INCENTIVES & TASK AUDIT LOGS
// ----------------------------------------------------
async function loadStaffDashboard() {
    try {
        const res = await fetch('/api/staff/my-dashboard');
        const data = await res.json();
        if (!res.ok || data.status !== 'success') {
            return;
        }
        const m = data.metrics || {};
        const pendingEl = document.getElementById('staff-metric-pending');
        const inProgEl = document.getElementById('staff-metric-in-progress');
        const completedEl = document.getElementById('staff-metric-completed');
        if (pendingEl) pendingEl.textContent = m.pending_tasks || 0;
        if (inProgEl) inProgEl.textContent = m.in_progress_tasks || 0;
        if (completedEl) completedEl.textContent = m.completed_this_month || 0;
        
        const gradeEl = document.getElementById('staff-metric-grade');
        if (gradeEl) {
            gradeEl.textContent = `Grade ${m.grade || '--'}`;
        }
        
        const statusEl = document.getElementById('staff-grade-status');
        if (statusEl) {
            statusEl.textContent = m.grade_label || 'Grade Performance Status';
        }
        
        const badgeEl = document.getElementById('staff-bonus-badge');
        if (badgeEl) {
            badgeEl.textContent = `${m.bonus_badge || ''} • Monthly SLA: ${m.sla_rate || 100}% on-time completion`;
        }
        
        const slaEl = document.getElementById('staff-sla-rate');
        if (slaEl) {
            slaEl.textContent = `${m.sla_rate || 100}% On-Time`;
        }

        const tbody = document.getElementById('staff-my-tasks-table');
        if (tbody) {
            const tasks = data.my_tasks || data.active_tasks || [];
            if (tasks.length === 0) {
                tbody.innerHTML = `<tr><td colspan="6" style="text-align:center; padding:24px; color:#15803d; font-weight:600;">🎉 All caught up! Zero pending tasks.</td></tr>`;
            } else {
                tbody.innerHTML = tasks.map(t => {
                    const isOverdue = t.due_date && new Date(t.due_date) < new Date() && t.status !== 'Completed';
                    const priorityColor = t.priority === 'High' ? '#dc2626' : (t.priority === 'Medium' ? '#d97706' : '#64748b');
                    return `
                        <tr>
                            <td style="font-weight:600; color:#0f172a;">
                                <div style="display:flex; align-items:center; gap:8px; flex-wrap:wrap;">
                                    <span>${escapeHtml(t.title)}</span>
                                    <button type="button" class="btn-secondary btn-table" style="padding:2px 6px; font-size:0.7rem;" onclick="openTaskAuditModal(${t.id}, '${escapeHtml(t.title)}')"><i data-lucide="history"></i> Audit Log</button>
                                </div>
                            </td>
                            <td><span style="color:${priorityColor}; font-weight:700;">${t.priority}</span></td>
                            <td><span class="badge-status-neutral">${escapeHtml(t.department || 'General')}</span></td>
                            <td>${t.due_date ? (isOverdue ? `<span style="color:#dc2626; font-weight:700;">${t.due_date} (Overdue)</span>` : t.due_date) : '-'}</td>
                            <td><span class="badge-status-${t.status === 'Completed' ? 'success' : (t.status === 'In Progress' ? 'progress' : 'pending')}">${t.status}</span></td>
                            <td>
                                ${t.status !== 'In Progress' ? `<button type="button" class="btn-secondary btn-table" onclick="updateStaffTaskStatus(${t.id}, 'In Progress')">Start</button>` : ''}
                                ${t.status !== 'Completed' ? `<button type="button" class="btn-primary btn-table" onclick="updateStaffTaskStatus(${t.id}, 'Completed')">Complete</button>` : ''}
                            </td>
                        </tr>
                    `;
                }).join('');
            }
        }
        if (window.lucide && typeof window.lucide.createIcons === 'function') window.lucide.createIcons();
    } catch (e) {
        console.error('Staff dashboard load error:', e);
    }
}

async function updateStaffTaskStatus(taskId, newStatus) {
    try {
        const res = await fetch(`/api/admin/tasks/${taskId}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ status: newStatus })
        });
        const data = await res.json();
        if (!res.ok || data.status !== 'success') {
            alert(data.message || 'Could not update task status.');
            return;
        }
        loadStaffDashboard();
    } catch (e) {
        console.error('Task status update error:', e);
    }
}

async function loadTeamIncentivesReport() {
    try {
        const monthInput = document.getElementById('incentive-month-select');
        let selectedMonth = '';
        if (monthInput && monthInput.value) {
            selectedMonth = monthInput.value;
        } else {
            const now = new Date();
            selectedMonth = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`;
            if (monthInput) monthInput.value = selectedMonth;
        }

        const res = await fetch(`/api/admin/tasks/incentives-report?month=${encodeURIComponent(selectedMonth)}`);
        const data = await res.json();
        if (!res.ok || data.status !== 'success') {
            return;
        }

        const tbody = document.getElementById('admin-incentives-table');
        if (tbody) {
            const leaderboard = data.leaderboard || [];
            if (leaderboard.length === 0) {
                tbody.innerHTML = `<tr><td colspan="9" style="text-align:center; padding:24px; color:#64748b;">No active team members found.</td></tr>`;
            } else {
                tbody.innerHTML = leaderboard.map((item, idx) => {
                    const s = item.staff || {};
                    const gradeBg = item.grade === 'A+' ? '#dcfce7; color:#15803d; border:1px solid #bbf7d0;' :
                                    (item.grade === 'A' ? '#e0f2fe; color:#0369a1; border:1px solid #bae6fd;' :
                                    (item.grade === 'B' ? '#fef3c7; color:#b45309; border:1px solid #fde68a;' :
                                    (item.grade === 'C' ? '#ffedd5; color:#c2410c; border:1px solid #fed7aa;' : '#fee2e2; color:#b91c1c; border:1px solid #fca5a5;')));
                    
                    return `
                        <tr>
                            <td style="font-weight:700; color:#0f172a;">#${idx + 1}</td>
                            <td style="font-weight:600; color:#0f172a;">${escapeHtml(s.full_name || 'Staff')} <div style="font-size:0.75rem; color:#64748b;">${escapeHtml(s.email || '')}</div></td>
                            <td><span class="badge-status-neutral">${escapeHtml(s.department || 'General')}</span></td>
                            <td style="font-weight:600;">${item.assigned_count}</td>
                            <td style="font-weight:700; color:#15803d;">${item.completed_count}</td>
                            <td style="font-weight:600; color:#0284c7;">${item.sla_rate}%</td>
                            <td style="color:${item.overdue_count > 0 ? '#dc2626' : '#64748b'}; font-weight:${item.overdue_count > 0 ? '700' : '400'};">${item.overdue_count}</td>
                            <td>
                                <span style="display:inline-block; padding:4px 10px; border-radius:12px; font-weight:800; font-size:0.8rem; ${gradeBg}">
                                    Grade ${item.grade}
                                </span>
                            </td>
                            <td>
                                <div style="font-weight:600; font-size:0.8rem; color:#0f172a;">${escapeHtml(item.grade_label)}</div>
                                <div style="font-size:0.75rem; color:#64748b;">${escapeHtml(item.bonus_badge)}</div>
                            </td>
                        </tr>
                    `;
                }).join('');
            }
        }
        if (window.lucide && typeof window.lucide.createIcons === 'function') window.lucide.createIcons();
    } catch (e) {
        console.error('Team incentives report load error:', e);
    }
}

async function openTaskAuditModal(taskId, taskTitle) {
    const modal = document.getElementById('modal-task-audit-logs');
    const titleEl = document.getElementById('task-audit-title');
    const bodyEl = document.getElementById('task-audit-logs-body');
    if (!modal || !bodyEl) return;
    
    if (titleEl) titleEl.textContent = `Audit Trail for Task #${taskId}: ${taskTitle || ''}`;
    bodyEl.innerHTML = `<div style="text-align:center; padding:20px; color:#64748b;"><i data-lucide="loader" class="spin"></i> Loading audit trail...</div>`;
    modal.style.display = 'flex';
    if (window.lucide && typeof window.lucide.createIcons === 'function') window.lucide.createIcons();

    try {
        const res = await fetch(`/api/admin/tasks/${taskId}/audit-logs`);
        const data = await res.json();
        if (!res.ok || data.status !== 'success') {
            bodyEl.innerHTML = `<div style="color:#dc2626; text-align:center; padding:20px;">Could not load audit logs.</div>`;
            return;
        }
        const logs = data.audit_logs || [];
        if (logs.length === 0) {
            bodyEl.innerHTML = `<div style="text-align:center; padding:20px; color:#64748b;">No audit logs recorded for this task yet.</div>`;
        } else {
            bodyEl.innerHTML = logs.map(l => `
                <div style="border-bottom:1px solid #e2e8f0; padding:10px 0; display:flex; justify-content:space-between; align-items:flex-start; gap:12px;">
                    <div>
                        <div style="font-weight:700; color:#0f172a;">${escapeHtml(l.action || '')}</div>
                        <div style="color:#334155; margin-top:2px;">${escapeHtml(l.details || '')}</div>
                        <div style="font-size:0.75rem; color:#64748b; margin-top:4px;">By: ${escapeHtml(l.actor_name || 'System')} (${l.actor_role || 'Staff'})</div>
                    </div>
                    <div style="font-size:0.75rem; color:#64748b; white-space:nowrap;">${l.created_at || ''}</div>
                </div>
            `).join('');
        }
    } catch (e) {
        console.error('Task audit logs fetch error:', e);
        bodyEl.innerHTML = `<div style="color:#dc2626; text-align:center; padding:20px;">Error loading audit trail.</div>`;
    }
}

function closeTaskAuditModal() {
    const modal = document.getElementById('modal-task-audit-logs');
    if (modal) modal.style.display = 'none';
}

function escapeJsString(value) {
    return String(value == null ? '' : value)
        .replace(/\\/g, '\\\\')
        .replace(/'/g, "\\'")
        .replace(/"/g, '\\"')
        .replace(/\n/g, '\\n')
        .replace(/\r/g, '\\r');
}

/* ====================================================
   MANUAL ORDER CREATION (B2B CLIENT & STAFF)
   ==================================================== */

let manualOrderLineItems = [];

async function openManualOrderModal(prefillData) {
    const modal = document.getElementById('modal-manual-order');
    const form = document.getElementById('manual-order-form');
    const err = document.getElementById('manual-order-error');
    if (!modal) return;

    modal.style.display = 'flex';
    modal.classList.add('active');

    if (form) form.reset();
    if (err) {
        err.style.display = 'none';
        err.textContent = '';
    }

    const clientSection = document.getElementById('manual-order-client-section');
    const existingWrap = document.getElementById('manual-order-existing-wrap');
    const newWrap = document.getElementById('manual-order-new-wrap');
    const companyEmail = document.getElementById('manual-order-company-email');
    const ownerName = document.getElementById('manual-order-owner-name');

    manualOrderLineItems = [];

    // Populate service datalist for autocompletion
    populateManualOrderServiceList();

    const isClient = currentUser && currentUser.role === 'CLIENT';

    if (isClient) {
        if (clientSection) clientSection.style.display = 'none';
        if (existingWrap) existingWrap.hidden = true;
        if (newWrap) newWrap.hidden = true;

        if (companyEmail) {
            companyEmail.value = currentUser.email || '';
        }
        if (ownerName) {
            ownerName.value = currentUser.full_name || '';
        }

        // Load client's own companies into company select
        await loadClientCompaniesForManualOrderSelect();
    } else {
        if (clientSection) clientSection.style.display = 'block';
        const radio = document.querySelector('input[name="manual-order-client-mode"][value="existing"]');
        if (radio) radio.checked = true;
        syncManualOrderClientMode();
        await loadAdminClientsForManualOrderSelect();
    }

    // Handle prefilled service data (e.g. from Order Service button)
    if (prefillData && prefillData.service_name) {
        addManualOrderProductLine(prefillData.service_name, prefillData.price || 0);
    } else {
        addManualOrderProductLine();
    }

    modal.classList.add('active');
    safeCreateIcons();
}

function closeManualOrderModal() {
    const modal = document.getElementById('modal-manual-order');
    if (modal) modal.classList.remove('active');
}

function syncManualOrderClientMode() {
    const checked = document.querySelector('input[name="manual-order-client-mode"]:checked');
    const mode = checked ? checked.value : 'existing';
    const existingWrap = document.getElementById('manual-order-existing-wrap');
    const newWrap = document.getElementById('manual-order-new-wrap');
    if (existingWrap) existingWrap.hidden = (mode !== 'existing');
    if (newWrap) newWrap.hidden = (mode !== 'new');
}

async function loadClientCompaniesForManualOrderSelect() {
    const select = document.getElementById('manual-order-company');
    if (!select) return;
    select.innerHTML = '<option value="">No company / create with name below</option>';
    try {
        const res = await fetch('/api/client/companies', { credentials: 'same-origin' });
        const data = await res.json().catch(() => ({}));
        if (data.status === 'success' && Array.isArray(data.companies)) {
            data.companies.forEach(c => {
                const opt = document.createElement('option');
                opt.value = c.id;
                opt.textContent = c.name;
                select.appendChild(opt);
            });
        }
    } catch (err) { console.error(err); }
}

async function loadAdminClientsForManualOrderSelect() {
    const select = document.getElementById('manual-order-client');
    if (!select) return;
    select.innerHTML = '<option value="">-- Select Client --</option>';
    try {
        const res = await fetch('/api/admin/customers?limit=500', { credentials: 'same-origin' });
        const data = await res.json().catch(() => ({}));
        const customers = data.customers || data.users || [];
        if (Array.isArray(customers)) {
            customers.forEach(c => {
                const opt = document.createElement('option');
                opt.value = c.id;
                opt.textContent = `${c.full_name || 'Client'} (${c.email})`;
                select.appendChild(opt);
            });
        }
    } catch (err) { console.error(err); }
}

async function onManualOrderClientChange() {
    const clientSelect = document.getElementById('manual-order-client');
    const companySelect = document.getElementById('manual-order-company');
    const companyEmail = document.getElementById('manual-order-company-email');
    if (!clientSelect || !companySelect) return;

    const clientId = clientSelect.value;
    companySelect.innerHTML = '<option value="">No company / create with name below</option>';
    if (!clientId) return;

    // Auto-fill B2B client account owner email for notification routing
    const selectedOpt = clientSelect.options[clientSelect.selectedIndex];
    if (selectedOpt && selectedOpt.textContent && companyEmail) {
        const match = selectedOpt.textContent.match(/\(([^)]+)\)/);
        if (match && match[1]) {
            companyEmail.value = match[1];
        }
    }

    try {
        const res = await fetch(`/api/admin/companies?client_id=${clientId}`, { credentials: 'same-origin' });
        const data = await res.json().catch(() => ({}));
        const comps = data.companies || [];
        if (Array.isArray(comps)) {
            comps.forEach(c => {
                const opt = document.createElement('option');
                opt.value = c.id;
                opt.textContent = c.name;
                companySelect.appendChild(opt);
            });
        }
    } catch (err) { console.error(err); }
}

async function populateManualOrderServiceList() {
    const datalist = document.getElementById('manual-order-service-list');
    if (!datalist) return;
    datalist.innerHTML = '';
    try {
        const endpoint = (currentUser && currentUser.role === 'CLIENT') ? '/api/client/services' : '/api/admin/services';
        const res = await fetch(endpoint, { credentials: 'same-origin' });
        const data = await res.json().catch(() => ({}));
        const services = data.services || [];
        if (Array.isArray(services)) {
            services.forEach(s => {
                const opt = document.createElement('option');
                opt.value = s.name;
                opt.setAttribute('data-price', s.price || 0);
                datalist.appendChild(opt);
            });
        }
    } catch (err) { console.error(err); }
}

function addManualOrderProductLine(name = '', price = '') {
    manualOrderLineItems.push({
        id: 'line_' + Date.now() + '_' + Math.random().toString(36).substr(2, 4),
        product_name: name,
        price: price
    });
    renderManualOrderProductLines();
}

function removeManualOrderProductLine(id) {
    manualOrderLineItems = manualOrderLineItems.filter(item => item.id !== id);
    if (manualOrderLineItems.length === 0) {
        addManualOrderProductLine();
        return;
    }
    renderManualOrderProductLines();
}

function updateManualOrderLineItem(id, field, value) {
    const item = manualOrderLineItems.find(i => i.id === id);
    if (!item) return;
    item[field] = value;

    if (field === 'product_name') {
        const datalist = document.getElementById('manual-order-service-list');
        if (datalist) {
            const match = Array.from(datalist.options).find(opt => opt.value === value);
            if (match && match.getAttribute('data-price')) {
                item.price = match.getAttribute('data-price');
                const priceInput = document.getElementById(`line-price-${id}`);
                if (priceInput) priceInput.value = item.price;
            }
        }
    }

    recalculateManualOrderTotal();
}

function renderManualOrderProductLines() {
    const container = document.getElementById('manual-order-products-container');
    if (!container) return;
    container.innerHTML = manualOrderLineItems.map((item) => `
        <div style="display:grid; grid-template-columns:1fr 110px 32px; gap:8px; align-items:center;">
            <input type="text" list="manual-order-service-list" class="select-filter" style="width:100%; font-size:0.85rem;" placeholder="Service / Product name" value="${escapeHtml(item.product_name || '')}" oninput="updateManualOrderLineItem('${item.id}', 'product_name', this.value)">
            <input type="number" id="line-price-${item.id}" step="0.01" min="0" class="select-filter" style="width:100%; font-size:0.85rem;" placeholder="Price (£)" value="${item.price !== '' ? item.price : ''}" oninput="updateManualOrderLineItem('${item.id}', 'price', this.value)">
            <button type="button" class="btn-secondary" style="padding:4px; text-align:center; color:#ef4444; border-color:#fca5a5;" title="Remove item" onclick="removeManualOrderProductLine('${item.id}')"><i data-lucide="trash-2" style="width:14px; height:14px;"></i></button>
        </div>
    `).join('');
    recalculateManualOrderTotal();
    safeCreateIcons();
}

function recalculateManualOrderTotal() {
    let total = 0;
    manualOrderLineItems.forEach(item => {
        const p = parseFloat(item.price);
        if (!isNaN(p) && p > 0) total += p;
    });
    const totalEl = document.getElementById('manual-order-total-price');
    if (totalEl) totalEl.textContent = total.toFixed(2);
}

async function submitManualOrderForm(event) {
    if (event) event.preventDefault();
    const err = document.getElementById('manual-order-error');
    const submitBtn = document.getElementById('manual-order-submit');
    if (err) { err.style.display = 'none'; err.textContent = ''; }

    const isClient = currentUser && currentUser.role === 'CLIENT';

    const line_items = manualOrderLineItems.map(item => ({
        product_name: (item.product_name || '').trim(),
        price: parseFloat(item.price) || 0
    })).filter(item => item.product_name !== '');

    if (line_items.length === 0) {
        if (err) { err.style.display = 'block'; err.textContent = 'Please add at least one product or service.'; }
        return;
    }

    const payload = {
        company_id: document.getElementById('manual-order-company')?.value || '',
        company_name: document.getElementById('manual-order-company-name')?.value || '',
        company_type: document.getElementById('manual-order-company-type')?.value || 'Private Limited Company by Shares (LTD)',
        sic_code: document.getElementById('manual-order-sic-code')?.value || '',
        company_email: document.getElementById('manual-order-company-email')?.value || '',
        company_phone: document.getElementById('manual-order-company-phone')?.value || '',
        reg_office: [
            document.getElementById('manual-order-reg-address')?.value || '',
            document.getElementById('manual-order-reg-city')?.value || '',
            document.getElementById('manual-order-reg-postcode')?.value || ''
        ].filter(Boolean).join(', '),
        registered_address_line1: document.getElementById('manual-order-reg-address')?.value || '',
        registered_city: document.getElementById('manual-order-reg-city')?.value || '',
        registered_postcode: document.getElementById('manual-order-reg-postcode')?.value || '',
        owner_name: document.getElementById('manual-order-owner-name')?.value || '',
        director_phone: document.getElementById('manual-order-director-phone')?.value || '',
        director_nationality: document.getElementById('manual-order-director-nationality')?.value || 'British',
        director_residence: document.getElementById('manual-order-director-residence')?.value || 'United Kingdom',
        payment_mode: document.getElementById('manual-order-payment-mode')?.value || 'Manual CRM',
        status: document.getElementById('manual-order-initial-status')?.value || 'Processing',
        notes: document.getElementById('manual-order-notes')?.value || '',
        line_items: line_items
    };

    if (!isClient) {
        const checkedMode = document.querySelector('input[name="manual-order-client-mode"]:checked');
        const mode = checkedMode ? checkedMode.value : 'existing';
        payload.client_mode = mode;

        if (mode === 'existing') {
            payload.client_id = document.getElementById('manual-order-client')?.value || '';
            if (!payload.client_id) {
                if (err) { err.style.display = 'block'; err.textContent = 'Please select a client.'; }
                return;
            }
        } else {
            payload.new_client_full_name = document.getElementById('manual-order-new-name')?.value || '';
            payload.new_client_email = document.getElementById('manual-order-new-email')?.value || '';
            if (!payload.new_client_full_name || !payload.new_client_email) {
                if (err) { err.style.display = 'block'; err.textContent = 'Please enter the new client name and email.'; }
                return;
            }
        }
    } else {
        payload.client_mode = 'existing';
        payload.client_id = currentUser.id;
    }

    if (!payload.company_email) {
        if (err) { err.style.display = 'block'; err.textContent = 'Please enter the company notification email.'; }
        return;
    }

    if (submitBtn) submitBtn.disabled = true;

    try {
        const endpoint = isClient ? '/api/client/orders' : '/api/admin/orders';
        const res = await fetch(endpoint, {
            method: 'POST',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data.status !== 'success') {
            throw new Error(data.message || 'Could not create order.');
        }

        closeManualOrderModal();

        // Refresh appropriate view
        if (isClient || activeView === 'client-orders') {
            if (typeof loadClientOrders === 'function') loadClientOrders();
        }
        if (!isClient || activeView === 'admin-orders') {
            if (typeof loadAdminOrders === 'function') loadAdminOrders();
        }

        alert(data.message || 'Order created successfully!');
    } catch (ex) {
        if (err) {
            err.style.display = 'block';
            err.textContent = ex.message || 'Could not create order.';
        }
    } finally {
        if (submitBtn) submitBtn.disabled = false;
    }
}

/* ====================================================
   UNIVERSAL CUSTOMER-CENTRIC ORDER CREATION ENGINE (5-STEP WIZARD)
   ==================================================== */
let universalOrderWizardState = {
    currentStep: 1,
    selectedCustomer: null,
    selectedProduct: null,
    customersList: [],
    catalogProducts: [],
    activeCustomerTypeFilter: 'All',
    activeCategory: 'All',
    formValues: {},
    repeatableData: {},
    uploadedDocs: {},
    draftOrderId: null,
    draftOrderNumber: null
};

async function openUniversalOrderWizard(prefillData = null) {
    const modal = document.getElementById('modal-universal-order-wizard');
    if (!modal) {
        if (typeof openManualOrderModal === 'function') openManualOrderModal(prefillData);
        return;
    }

    // Show modal instantly on click (0ms latency)
    modal.style.display = 'flex';
    modal.classList.add('active');
    hideWizardError();

    let activeUser = getCurrentUser();
    if (!activeUser) {
        try {
            const res = await fetch('/api/auth/me', { credentials: 'same-origin' });
            const data = await res.json().catch(() => ({}));
            if (data.status === 'success' && data.user) {
                currentUser = data.user;
                if (typeof writeCachedAuthUser === 'function') writeCachedAuthUser(data.user);
                activeUser = data.user;
            }
        } catch (_) {}
    }

    const isClient = !!(activeUser && activeUser.role === 'CLIENT');

    universalOrderWizardState = {
        currentStep: 1,
        selectedCustomer: isClient ? activeUser : null,
        selectedProduct: null,
        customersList: [],
        catalogProducts: [],
        activeCustomerTypeFilter: 'All',
        activeCategory: 'All',
        formValues: {},
        repeatableData: {},
        uploadedDocs: {},
        draftOrderId: null,
        draftOrderNumber: null
    };

    const adminCustomerPane = document.getElementById('wizard-admin-customer-select-pane');
    const clientCustomerPane = document.getElementById('wizard-client-customer-summary-pane');

    if (isClient) {
        if (adminCustomerPane) adminCustomerPane.style.display = 'none';
        if (clientCustomerPane) {
            clientCustomerPane.style.display = 'block';
            const nameEl = document.getElementById('wizard-client-self-name');
            const emailEl = document.getElementById('wizard-client-self-email');
            if (nameEl) nameEl.textContent = (activeUser && activeUser.full_name) || 'Customer';
            if (emailEl) emailEl.textContent = (activeUser && activeUser.email) || '';

            const isB2B = !!(activeUser && (activeUser.is_b2b === 1 || activeUser.client_type === 'B2B'));
            const b2bWrap = document.getElementById('wizard-client-self-b2b-wrap');
            const b2bBadge = document.getElementById('wizard-client-self-b2b-badge');
            const typeLabel = document.getElementById('wizard-client-self-type-label');

            if (isB2B && activeUser.b2b_id) {
                if (typeLabel) typeLabel.textContent = 'B2B CORPORATE ACCOUNT';
                if (b2bBadge) b2bBadge.textContent = activeUser.b2b_id;
                if (b2bWrap) b2bWrap.style.display = 'block';
            } else {
                if (typeLabel) typeLabel.textContent = 'NORMAL CUSTOMER ACCOUNT';
                if (b2bWrap) b2bWrap.style.display = 'none';
            }
        }
        universalOrderWizardState.selectedCustomer = activeUser;
    } else {
        if (adminCustomerPane) adminCustomerPane.style.display = 'block';
        if (clientCustomerPane) clientCustomerPane.style.display = 'none';
    }

    // Open target step immediately so UI is responsive
    jumpToWizardStep(isClient ? 2 : 1);
    safeCreateIcons();

    // Fetch data asynchronously
    try {
        const promises = [fetchWizardCatalogProducts()];
        if (!isClient) promises.push(fetchWizardCustomersList());
        await Promise.all(promises);
    } catch (err) {
        console.error('Wizard initialization fetch error:', err);
    }

    if (prefillData && (prefillData.service_name || prefillData.id)) {
        const match = universalOrderWizardState.catalogProducts.find(p => 
            p.id == prefillData.id || 
            (p.name && prefillData.service_name && p.name.toLowerCase() === prefillData.service_name.toLowerCase())
        );
        if (match) {
            universalOrderWizardState.selectedProduct = match;
            jumpToWizardStep(3);
        }
    }

    safeCreateIcons();
}

function closeUniversalOrderWizard() {
    const modal = document.getElementById('modal-universal-order-wizard');
    if (modal) {
        modal.style.display = 'none';
        modal.classList.remove('active');
    }
}

async function fetchWizardCustomersList() {
    const grid = document.getElementById('wizard-customers-grid');
    if (grid) grid.innerHTML = '<div style="text-align:center; padding:40px; color:var(--color-text-muted); grid-column:1/-1;">Loading Customers...</div>';

    try {
        const res = await fetch('/api/admin/customers?limit=500', { credentials: 'same-origin' });
        const data = await res.json().catch(() => ({}));
        universalOrderWizardState.customersList = data.customers || data.users || [];
    } catch (err) {
        console.error('Customer fetch error:', err);
    }

    renderWizardCustomersGrid();
}

function setWizardCustomerTypeFilter(type) {
    universalOrderWizardState.activeCustomerTypeFilter = type;
    
    ['all', 'normal', 'b2b'].forEach(t => {
        const pill = document.getElementById(`pill-cust-${t}`);
        if (pill) {
            if ((t === 'all' && type === 'All') || (t === 'normal' && type === 'Normal') || (t === 'b2b' && type === 'B2B')) {
                pill.classList.add('active');
            } else {
                pill.classList.remove('active');
            }
        }
    });

    renderWizardCustomersGrid();
}

function filterWizardCustomers() {
    renderWizardCustomersGrid();
}

function renderWizardCustomersGrid() {
    const grid = document.getElementById('wizard-customers-grid');
    if (!grid) return;

    const search = (document.getElementById('wizard-customer-search')?.value || '').toLowerCase().trim();
    const typeFilter = universalOrderWizardState.activeCustomerTypeFilter;

    const filtered = universalOrderWizardState.customersList.filter(c => {
        const isB2B = (c.is_b2b === 1 || c.client_type === 'B2B');
        const matchesType = (typeFilter === 'All') ||
            (typeFilter === 'B2B' && isB2B) ||
            (typeFilter === 'Normal' && !isB2B);

        const nameMatch = (c.full_name || '').toLowerCase().includes(search);
        const emailMatch = (c.email || '').toLowerCase().includes(search);
        const phoneMatch = (c.phone || '').toLowerCase().includes(search);
        const b2bMatch = isB2B && (c.b2b_id || '').toLowerCase().includes(search);

        return matchesType && (!search || nameMatch || emailMatch || phoneMatch || b2bMatch);
    });

    if (filtered.length === 0) {
        grid.innerHTML = '<div style="text-align:center; padding:30px; color:var(--color-text-muted); grid-column:1/-1;">No matching customers found.</div>';
        return;
    }

    grid.innerHTML = filtered.map(c => {
        const isB2B = (c.is_b2b === 1 || c.client_type === 'B2B');
        const isSelected = (universalOrderWizardState.selectedCustomer && universalOrderWizardState.selectedCustomer.id === c.id);

        return `
            <div style="background:var(--color-surface); border:2px solid ${isSelected ? 'var(--color-primary)' : 'var(--color-border)'}; border-radius:12px; padding:16px; display:flex; flex-direction:column; justify-content:space-between; cursor:pointer; transition:all 0.15s ease;"
                 class="card-hover-effect" onclick="selectWizardCustomer(${c.id})">
                <div>
                    <div style="display:flex; justify-content:space-between; align-items:flex-start; margin-bottom:6px;">
                        <span class="status-badge ${isB2B ? 'active' : 'info'}" style="font-size:0.72rem;">
                            ${isB2B ? 'B2B Account' : 'Normal Customer'}
                        </span>
                        ${isB2B && c.b2b_id ? `<span style="font-weight:700; font-size:0.75rem; color:var(--color-success);">${escapeHtml(c.b2b_id)}</span>` : ''}
                    </div>
                    <h4 style="font-size:1.02rem; font-weight:700; color:var(--color-text-primary); margin:4px 0 2px 0;">${escapeHtml(c.full_name || 'Client')}</h4>
                    <div style="font-size:0.8rem; color:var(--color-text-secondary);">${escapeHtml(c.email)}</div>
                    ${c.phone ? `<div style="font-size:0.78rem; color:var(--color-text-muted); margin-top:2px;">📞 ${escapeHtml(c.phone)}</div>` : ''}
                </div>
                <div style="margin-top:12px; text-align:right;">
                    <button type="button" class="btn-${isSelected ? 'primary' : 'secondary'}" style="padding:4px 12px; font-size:0.78rem;">
                        ${isSelected ? 'Selected ✓' : 'Select Customer'}
                    </button>
                </div>
            </div>
        `;
    }).join('');

    safeCreateIcons();
}

function selectWizardCustomer(customerId) {
    const cust = universalOrderWizardState.customersList.find(c => c.id == customerId);
    if (!cust) return;

    universalOrderWizardState.selectedCustomer = cust;
    renderWizardCustomersGrid();
    jumpToWizardStep(2);
}

async function fetchWizardCatalogProducts() {
    const grid = document.getElementById('wizard-products-grid');
    if (grid) grid.innerHTML = '<div style="text-align:center; padding:40px; color:var(--color-text-muted); grid-column:1/-1;">Loading Services Catalog...</div>';

    try {
        const res = await fetch('/api/catalog/products', { credentials: 'same-origin' });
        const data = await res.json().catch(() => ({}));
        if (data.status === 'success' && Array.isArray(data.products)) {
            universalOrderWizardState.catalogProducts = data.products;
        } else {
            const res2 = await fetch('/api/client/services', { credentials: 'same-origin' });
            const data2 = await res2.json().catch(() => ({}));
            universalOrderWizardState.catalogProducts = data2.services || [];
        }
    } catch (err) {
        console.error('Catalog fetch error:', err);
    }

    renderWizardCategoryPills();
    renderWizardCatalogGrid();
}

function renderWizardCategoryPills() {
    const container = document.getElementById('wizard-category-pills');
    if (!container) return;

    const cats = ['All'];
    universalOrderWizardState.catalogProducts.forEach(p => {
        if (p.category && !cats.includes(p.category)) cats.push(p.category);
    });

    container.innerHTML = cats.map(cat => `
        <button type="button" class="badge ${universalOrderWizardState.activeCategory === cat ? 'active' : ''}" 
                style="cursor:pointer; border:1px solid var(--color-border); padding:6px 12px; font-size:0.78rem; ${universalOrderWizardState.activeCategory === cat ? 'background:var(--color-primary) !important; color:#FFFFFF !important;' : 'background:var(--color-surface); color:var(--color-text-primary);'}"
                onclick="setWizardCategoryFilter('${escapeJsString(cat)}')">
            ${escapeHtml(cat)}
        </button>
    `).join('');
}

function setWizardCategoryFilter(cat) {
    universalOrderWizardState.activeCategory = cat;
    renderWizardCategoryPills();
    renderWizardCatalogGrid();
}

function filterWizardCatalogProducts() {
    renderWizardCatalogGrid();
}

function renderWizardCatalogGrid() {
    const grid = document.getElementById('wizard-products-grid');
    if (!grid) return;

    const searchTerm = (document.getElementById('wizard-product-search')?.value || '').toLowerCase().trim();
    const cat = universalOrderWizardState.activeCategory;
    const selectedCust = universalOrderWizardState.selectedCustomer;
    const activeUser = getCurrentUser();
    const isB2BCust = (selectedCust && (selectedCust.is_b2b === 1 || selectedCust.client_type === 'B2B')) || (activeUser && (activeUser.is_b2b === 1 || activeUser.client_type === 'B2B'));

    const filtered = universalOrderWizardState.catalogProducts.filter(p => {
        const matchesCat = (cat === 'All' || p.category === cat);
        const matchesSearch = !searchTerm || 
            (p.name && p.name.toLowerCase().includes(searchTerm)) || 
            (p.description && p.description.toLowerCase().includes(searchTerm)) ||
            (p.category && p.category.toLowerCase().includes(searchTerm));
        return matchesCat && matchesSearch;
    });

    if (filtered.length === 0) {
        grid.innerHTML = '<div style="text-align:center; padding:40px; color:var(--color-text-muted); grid-column:1/-1;">No products found matching your search.</div>';
        return;
    }

    grid.innerHTML = filtered.map(p => `
        <div style="background:var(--color-surface); border:1px solid var(--color-border); border-radius:14px; padding:18px; display:flex; flex-direction:column; justify-content:space-between; transition:all 0.15s ease;" class="card-hover-effect">
            <div>
                <div style="display:flex; justify-content:space-between; align-items:flex-start; margin-bottom:8px;">
                    <span class="status-badge info">${escapeHtml(p.category || 'Service')}</span>
                    ${isB2BCust 
                        ? `<span style="font-weight:700; font-size:0.8rem; color:var(--color-primary); background:var(--color-primary-soft); padding:4px 8px; border-radius:6px;">Custom B2B Rate</span>`
                        : `<span style="font-weight:800; font-size:1.15rem; color:var(--color-primary);">£${parseFloat(p.price || 0).toFixed(2)}</span>`
                    }
                </div>
                <h4 style="font-size:1.02rem; font-weight:700; color:var(--color-text-primary); margin:4px 0 6px 0;">${escapeHtml(p.name)}</h4>
                <p style="font-size:0.8rem; color:var(--color-text-muted); line-height:1.4; margin-bottom:12px; display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden;">
                    ${escapeHtml(p.description || 'Professional service package.')}
                </p>
            </div>
            <div>
                <div style="font-size:0.75rem; color:var(--color-text-secondary); margin-bottom:12px; display:flex; gap:12px;">
                    <span><i data-lucide="clock" style="width:12px; height:12px; margin-right:2px;"></i> ${escapeHtml(p.estimated_delivery_time || '24-48 Hours')}</span>
                    <span><i data-lucide="file-text" style="width:12px; height:12px; margin-right:2px;"></i> ${p.document_requirements && p.document_requirements.length ? p.document_requirements.length + ' Docs' : 'Online Specs'}</span>
                </div>
                <button type="button" class="btn-primary" style="width:100%; justify-content:center; padding:8px 14px; font-size:0.84rem;" onclick="selectWizardProduct(${p.id})">
                    Select &amp; Configure <i data-lucide="arrow-right" style="width:14px; height:14px; margin-left:4px;"></i>
                </button>
            </div>
        </div>
    `).join('');

    safeCreateIcons();
}

function selectWizardProduct(productId) {
    const product = universalOrderWizardState.catalogProducts.find(p => p.id == productId);
    if (!product) return;

    universalOrderWizardState.selectedProduct = product;
    universalOrderWizardState.formValues = {};
    universalOrderWizardState.repeatableData = {};

    if (product.repeatable_sections && Array.isArray(product.repeatable_sections)) {
        product.repeatable_sections.forEach(sec => {
            universalOrderWizardState.repeatableData[sec.id] = [];
            const minEntries = sec.min_entries || 1;
            for (let i = 0; i < minEntries; i++) {
                addWizardRepeatableEntry(sec.id, false);
            }
        });
    }

    const cust = universalOrderWizardState.selectedCustomer;
    if (cust) {
        universalOrderWizardState.formValues['full_name'] = cust.full_name || '';
        universalOrderWizardState.formValues['email'] = cust.email || '';
        universalOrderWizardState.formValues['phone'] = cust.phone || '';
    }

    jumpToWizardStep(3);
}

function jumpToWizardStep(stepNum) {
    hideWizardError();
    const activeUser = getCurrentUser();
    const isClient = !!(activeUser && activeUser.role === 'CLIENT');
    if (isClient && stepNum === 1) {
        stepNum = 2;
    }

    const pill1 = document.getElementById('wizard-step-pill-1');
    if (pill1) {
        pill1.style.display = isClient ? 'none' : 'flex';
    }

    if (isClient && activeUser) {
        universalOrderWizardState.selectedCustomer = activeUser;
    }

    // Guard Step Navigation
    if (stepNum > 1 && !universalOrderWizardState.selectedCustomer) {
        if (isClient && activeUser) {
            universalOrderWizardState.selectedCustomer = activeUser;
        } else {
            showWizardError('Please select a customer first.');
            return;
        }
    }
    if (stepNum > 2 && !universalOrderWizardState.selectedProduct) {
        showWizardError('Please select a service/product first.');
        return;
    }
    if (stepNum === 4) {
        const valErr = validateWizardStep3Fields();
        if (valErr) {
            showWizardError(valErr);
            return;
        }
    }

    universalOrderWizardState.currentStep = stepNum;

    for (let i = 1; i <= 5; i++) {
        const pill = document.getElementById(`wizard-step-pill-${i}`);
        const pane = document.getElementById(`wizard-step-${i}`);
        if (pill) {
            if (i === stepNum) pill.classList.add('active');
            else if (i < stepNum) pill.classList.add('completed');
            else pill.classList.remove('active', 'completed');
        }
        if (pane) {
            pane.style.display = (i === stepNum) ? 'block' : 'none';
        }
    }

    const btnBack = document.getElementById('wizard-btn-back');
    const btnNext = document.getElementById('wizard-btn-next');
    const btnSubmit = document.getElementById('wizard-btn-submit');

    const minStep = isClient ? 2 : 1;
    if (btnBack) btnBack.style.display = (stepNum > minStep) ? 'inline-flex' : 'none';
    if (btnNext) btnNext.style.display = (stepNum < 5) ? 'inline-flex' : 'none';
    if (btnSubmit) btnSubmit.style.display = (stepNum === 5) ? 'inline-flex' : 'none';

    if (stepNum === 3) {
        renderWizardStep3Form();
    } else if (stepNum === 4) {
        renderWizardStep4Documents();
    } else if (stepNum === 5) {
        renderWizardStep5Review();
    }

    safeCreateIcons();
}

function navigateWizardStep(delta) {
    const target = universalOrderWizardState.currentStep + delta;
    if (target >= 1 && target <= 5) {
        jumpToWizardStep(target);
    }
}

function renderWizardStep3Form() {
    const p = universalOrderWizardState.selectedProduct;
    const cust = universalOrderWizardState.selectedCustomer;
    if (!p) return;

    const activeUser = getCurrentUser();
    const isB2B = (cust && (cust.is_b2b === 1 || cust.client_type === 'B2B')) || (activeUser && (activeUser.is_b2b === 1 || activeUser.client_type === 'B2B'));
    document.getElementById('wizard-selected-cat').textContent = p.category || 'Service';
    document.getElementById('wizard-selected-title').textContent = p.name;
    document.getElementById('wizard-selected-price').textContent = isB2B ? 'Custom B2B Rate' : `£${parseFloat(p.price || 0).toFixed(2)}`;
    document.getElementById('wizard-selected-est').textContent = `Est: ${p.estimated_delivery_time || '24-48 Hours'}`;
    const custSummary = document.getElementById('wizard-selected-customer-summary');
    if (custSummary) {
        if (isB2B && cust.b2b_id) {
            custSummary.innerHTML = `Customer: <strong>${escapeHtml(cust.full_name || 'Client')}</strong> (${escapeHtml(cust.email)}) • <span style="color:var(--color-success); font-weight:700;">${escapeHtml(cust.b2b_id)}</span>`;
        } else {
            custSummary.innerHTML = `Customer: <strong>${escapeHtml((cust || {}).full_name || 'Client')}</strong> (${escapeHtml((cust || {}).email || '')})`;
        }
    }

    const fieldsContainer = document.getElementById('wizard-dynamic-fields-container');
    if (fieldsContainer) {
        const fields = p.form_config || [];
        if (fields.length === 0) {
            fieldsContainer.innerHTML = '<div style="font-size:0.85rem; color:var(--color-text-muted);">No specific custom fields required for this product. Click Continue to proceed.</div>';
        } else {
            fieldsContainer.innerHTML = fields.map(f => renderSingleWizardFieldHTML(f)).join('');
        }
    }

    renderWizardRepeatableSections();
}

function renderSingleWizardFieldHTML(f) {
    const val = universalOrderWizardState.formValues[f.id] !== undefined ? universalOrderWizardState.formValues[f.id] : (f.default_value || '');
    const isHidden = shouldHideWizardConditionalField(f);

    let inputHTML = '';
    const fieldType = (f.type || 'text').toLowerCase();

    if (fieldType === 'textarea' || fieldType === 'long text') {
        inputHTML = `<textarea id="wfield-${f.id}" class="select-filter" style="width:100%; margin-top:4px; min-height:70px; font-size:0.88rem; padding:10px 14px;" placeholder="${escapeHtml(f.placeholder || '')}" onchange="updateWizardFieldValue('${f.id}', this.value)">${escapeHtml(val)}</textarea>`;
    } else if (fieldType === 'dropdown' || fieldType === 'select') {
        const opts = f.options || [];
        inputHTML = `
            <select id="wfield-${f.id}" class="select-filter" style="width:100%; margin-top:4px; font-size:0.88rem; padding:10px 14px;" onchange="updateWizardFieldValue('${f.id}', this.value)">
                ${opts.map(o => `<option value="${escapeHtml(o)}" ${val === o ? 'selected' : ''}>${escapeHtml(o)}</option>`).join('')}
            </select>`;
    } else if (fieldType === 'radio') {
        const opts = f.options || ['YES', 'NO'];
        inputHTML = `
            <div style="display:flex; gap:16px; margin-top:6px;">
                ${opts.map(o => `
                    <label style="font-size:0.85rem; font-weight:600; cursor:pointer; color:var(--color-text-primary);">
                        <input type="radio" name="wfield-${f.id}" value="${escapeHtml(o)}" ${val === o ? 'checked' : ''} onchange="updateWizardFieldValue('${f.id}', this.value)" style="accent-color:var(--color-primary);"> ${escapeHtml(o)}
                    </label>
                `).join('')}
            </div>`;
    } else {
        const inputType = (fieldType === 'email') ? 'email' : (fieldType === 'phone') ? 'tel' : (fieldType === 'number') ? 'number' : (fieldType === 'date') ? 'date' : 'text';
        inputHTML = `<input type="${inputType}" id="wfield-${f.id}" class="select-filter" style="width:100%; margin-top:4px; font-size:0.88rem; padding:10px 14px;" value="${escapeHtml(val)}" placeholder="${escapeHtml(f.placeholder || '')}" autocomplete="off" oninput="updateWizardFieldValue('${f.id}', this.value)">`;
    }

    return `
        <div id="wfield-wrap-${f.id}" style="${isHidden ? 'display:none;' : ''}">
            <label for="wfield-${f.id}" style="font-size:0.82rem; font-weight:700; color:var(--color-text-primary);">
                ${escapeHtml(f.label)} ${f.required ? '<span style="color:var(--color-danger);">*</span>' : ''}
            </label>
            ${inputHTML}
            ${f.help_text ? `<div style="font-size:0.75rem; color:var(--color-text-muted); margin-top:4px;">${escapeHtml(f.help_text)}</div>` : ''}
        </div>
    `;
}

function shouldHideWizardConditionalField(f) {
    if (!f.depends_on) return false;
    const parentVal = universalOrderWizardState.formValues[f.depends_on];
    if (f.condition === 'equals') {
        return parentVal !== f.condition_value;
    }
    return false;
}

function updateWizardFieldValue(fieldId, value) {
    universalOrderWizardState.formValues[fieldId] = value;
    
    const p = universalOrderWizardState.selectedProduct;
    if (p && p.form_config) {
        p.form_config.forEach(f => {
            if (f.depends_on === fieldId) {
                const wrap = document.getElementById(`wfield-wrap-${f.id}`);
                if (wrap) {
                    const hide = shouldHideWizardConditionalField(f);
                    wrap.style.display = hide ? 'none' : 'block';
                }
            }
        });
    }
}

function renderWizardRepeatableSections() {
    const container = document.getElementById('wizard-repeatable-sections-container');
    if (!container) return;

    const p = universalOrderWizardState.selectedProduct;
    const sections = p.repeatable_sections || [];
    if (sections.length === 0) {
        container.innerHTML = '';
        return;
    }

    container.innerHTML = sections.map(sec => {
        const entries = universalOrderWizardState.repeatableData[sec.id] || [];
        return `
            <div style="background:var(--color-surface-soft); border:1px solid var(--color-border); border-radius:14px; padding:20px;">
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:14px;">
                    <h5 style="font-size:0.92rem; font-weight:700; color:var(--color-text-primary); margin:0;">${escapeHtml(sec.title)} (${entries.length})</h5>
                    <button type="button" class="btn-secondary" style="padding:6px 12px; font-size:0.8rem;" onclick="addWizardRepeatableEntry('${sec.id}')">
                        <i data-lucide="plus" style="width:14px; height:14px; margin-right:4px;"></i> ${escapeHtml(sec.button_label || 'Add Entry')}
                    </button>
                </div>
                <div style="display:flex; flex-direction:column; gap:12px;">
                    ${entries.map((entry, idx) => `
                        <div style="background:var(--color-surface); border:1px solid var(--color-border); border-radius:10px; padding:14px; position:relative;">
                            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:10px;">
                                <strong style="font-size:0.82rem; color:var(--color-primary);">${sec.title.slice(0, -1) || 'Entry'} #${idx + 1}</strong>
                                ${entries.length > (sec.min_entries || 1) ? `
                                    <button type="button" class="btn-ghost" style="color:var(--color-danger); padding:2px 8px; font-size:0.75rem;" onclick="removeWizardRepeatableEntry('${sec.id}', ${idx})">Remove</button>
                                ` : ''}
                            </div>
                            <div style="display:grid; grid-template-columns:1fr 1fr; gap:10px;">
                                ${(sec.fields || []).map(sf => `
                                    <div>
                                        <label style="font-size:0.78rem; font-weight:700; color:var(--color-text-primary);">${escapeHtml(sf.label)}</label>
                                        <input type="${sf.type === 'email' ? 'email' : sf.type === 'number' ? 'number' : sf.type === 'date' ? 'date' : 'text'}" 
                                               class="select-filter" style="width:100%; margin-top:2px; font-size:0.82rem; padding:8px 10px;" 
                                               value="${escapeHtml(entry[sf.id] || '')}" 
                                               oninput="updateWizardRepeatableFieldValue('${sec.id}', ${idx}, '${sf.id}', this.value)">
                                    </div>
                                `).join('')}
                            </div>
                        </div>
                    `).join('')}
                </div>
            </div>
        `;
    }).join('');

    safeCreateIcons();
}

function addWizardRepeatableEntry(sectionId, render = true) {
    if (!universalOrderWizardState.repeatableData[sectionId]) {
        universalOrderWizardState.repeatableData[sectionId] = [];
    }
    universalOrderWizardState.repeatableData[sectionId].push({});
    if (render) renderWizardRepeatableSections();
}

function removeWizardRepeatableEntry(sectionId, idx) {
    if (universalOrderWizardState.repeatableData[sectionId]) {
        universalOrderWizardState.repeatableData[sectionId].splice(idx, 1);
        renderWizardRepeatableSections();
    }
}

function updateWizardRepeatableFieldValue(sectionId, idx, fieldId, value) {
    if (universalOrderWizardState.repeatableData[sectionId] && universalOrderWizardState.repeatableData[sectionId][idx]) {
        universalOrderWizardState.repeatableData[sectionId][idx][fieldId] = value;
    }
}

function validateWizardStep3Fields() {
    const p = universalOrderWizardState.selectedProduct;
    if (!p || !p.form_config) return null;

    for (let f of p.form_config) {
        if (f.required && !shouldHideWizardConditionalField(f)) {
            const val = (universalOrderWizardState.formValues[f.id] || '').toString().trim();
            if (!val) {
                return `Please complete required field: ${f.label}`;
            }
        }
    }
    return null;
}

function renderWizardStep4Documents() {
    const container = document.getElementById('wizard-documents-checklist');
    if (!container) return;

    const p = universalOrderWizardState.selectedProduct;
    const reqs = p.document_requirements || [];

    if (reqs.length === 0) {
        container.innerHTML = '<div style="font-size:0.85rem; color:var(--color-text-muted); text-align:center; padding:30px;">No document uploads required for this product. Click Continue to review your order.</div>';
        return;
    }

    container.innerHTML = reqs.map(doc => {
        const uploaded = universalOrderWizardState.uploadedDocs[doc.id];
        const statusText = uploaded ? uploaded.status : (doc.required ? 'Required' : 'Optional');
        const statusClass = uploaded ? 'completed' : (doc.required ? 'pending' : 'info');

        return `
            <div style="background:var(--color-surface-soft); border:1px solid var(--color-border); border-radius:12px; padding:16px 20px; display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:12px;">
                <div>
                    <div style="display:flex; align-items:center; gap:8px;">
                        <strong style="font-size:0.9rem; color:var(--color-text-primary);">${escapeHtml(doc.name)}</strong>
                        <span class="status-badge ${statusClass}">${statusText}</span>
                    </div>
                    <p style="font-size:0.8rem; color:var(--color-text-muted); margin:4px 0 0 0;">${escapeHtml(doc.description || '')}</p>
                    ${uploaded ? `<div style="font-size:0.78rem; color:var(--color-success); font-weight:600; margin-top:4px;">✓ ${escapeHtml(uploaded.file_name)}</div>` : ''}
                </div>
                <div>
                    <input type="file" id="doc-file-input-${doc.id}" style="display:none;" onchange="handleWizardFileUpload('${doc.id}', this)">
                    <button type="button" class="btn-secondary" style="font-size:0.82rem; padding:8px 14px;" onclick="document.getElementById('doc-file-input-${doc.id}').click()">
                        <i data-lucide="upload-cloud" style="width:14px; height:14px; margin-right:4px;"></i> ${uploaded ? 'Replace Document' : 'Upload File'}
                    </button>
                </div>
            </div>
        `;
    }).join('');

    safeCreateIcons();
}

async function handleWizardFileUpload(docReqId, fileInput) {
    if (!fileInput.files || !fileInput.files[0]) return;
    const file = fileInput.files[0];

    showWizardError(`Uploading ${file.name}...`, false);

    const formData = new FormData();
    formData.append('document', file);
    formData.append('document_type', docReqId);

    try {
        const res = await fetch('/api/client/documents/upload', {
            method: 'POST',
            credentials: 'same-origin',
            body: formData
        });
        const data = await res.json().catch(() => ({}));
        if (res.ok && (data.status === 'success' || data.document_id)) {
            hideWizardError();
            universalOrderWizardState.uploadedDocs[docReqId] = {
                document_id: data.document_id || data.id,
                file_name: file.name,
                file_path: data.file_path || '',
                status: 'Uploaded'
            };
            renderWizardStep4Documents();
        } else {
            showWizardError(data.message || 'File upload failed. Please try again.');
        }
    } catch (err) {
        showWizardError('Document upload error: ' + err.message);
    }
}

function renderWizardStep5Review() {
    const p = universalOrderWizardState.selectedProduct;
    const cust = universalOrderWizardState.selectedCustomer;
    if (!p) return;

    const price = parseFloat(p.price || 0);
    const vat = roundToTwo(price * 0.20);
    const total = roundToTwo(price + vat);

    const activeUser = getCurrentUser();
    const isB2B = (cust && (cust.is_b2b === 1 || cust.client_type === 'B2B')) || (activeUser && (activeUser.is_b2b === 1 || activeUser.client_type === 'B2B'));
    document.getElementById('review-service-category').textContent = (p.category || 'Service').toUpperCase();
    document.getElementById('review-service-name').textContent = p.name;
    if (isB2B) {
        document.getElementById('review-total-price').textContent = 'Custom B2B Rate';
        document.getElementById('review-vat-breakdown').textContent = 'Admin will assign custom rate upon order review.';
    } else {
        document.getElementById('review-total-price').textContent = `£${total.toFixed(2)}`;
        document.getElementById('review-vat-breakdown').textContent = `Subtotal £${price.toFixed(2)} + VAT (20%) £${vat.toFixed(2)}`;
    }
    const custIdentity = document.getElementById('review-customer-identity');
    if (custIdentity) {
        if (isB2B && cust.b2b_id) {
            custIdentity.innerHTML = `Customer: <strong>${escapeHtml(cust.full_name || 'Client')}</strong> (${escapeHtml(cust.email)}) • <span class="status-badge active">${escapeHtml(cust.b2b_id)}</span>`;
        } else {
            custIdentity.innerHTML = `Customer: <strong>${escapeHtml((cust || {}).full_name || 'Client')}</strong> (${escapeHtml((cust || {}).email || '')})`;
        }
    }

    const fieldsErr = validateWizardStep3Fields();
    const fieldsIcon = document.getElementById('review-fields-check-icon');
    const fieldsTitle = document.getElementById('review-fields-check-title');

    if (fieldsErr) {
        if (fieldsIcon) { fieldsIcon.setAttribute('data-lucide', 'alert-circle'); fieldsIcon.style.color = 'var(--color-danger)'; }
        if (fieldsTitle) fieldsTitle.textContent = 'Missing Product Information';
    } else {
        if (fieldsIcon) { fieldsIcon.setAttribute('data-lucide', 'check-circle'); fieldsIcon.style.color = 'var(--color-success)'; }
        if (fieldsTitle) fieldsTitle.textContent = 'Product Information Complete';
    }

    const summaryContainer = document.getElementById('review-form-values-summary');
    if (summaryContainer) {
        const entries = Object.entries(universalOrderWizardState.formValues);
        if (entries.length === 0) {
            summaryContainer.innerHTML = '<div style="color:var(--color-text-muted);">Standard catalog order specifications.</div>';
        } else {
            summaryContainer.innerHTML = entries.map(([k, v]) => `
                <div><span style="color:var(--color-text-muted);">${escapeHtml(k)}:</span> <strong>${escapeHtml(v)}</strong></div>
            `).join('');
        }
    }

    safeCreateIcons();
}

async function saveUniversalOrderDraft() {
    const p = universalOrderWizardState.selectedProduct;
    const cust = universalOrderWizardState.selectedCustomer;
    if (!p) {
        showWizardError('Please select a product first.');
        return;
    }

    const payload = {
        service_id: p.id,
        service_name: p.name,
        client_id: (cust || {}).id,
        current_step: universalOrderWizardState.currentStep,
        form_values: universalOrderWizardState.formValues,
        repeatable_data: universalOrderWizardState.repeatableData,
        order_id: universalOrderWizardState.draftOrderId,
        notes: document.getElementById('wizard-order-notes')?.value || ''
    };

    try {
        const res = await fetch('/api/orders/draft', {
            method: 'POST',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const data = await res.json().catch(() => ({}));
        if (res.ok && data.status === 'success') {
            universalOrderWizardState.draftOrderId = data.order_id;
            universalOrderWizardState.draftOrderNumber = data.order_number;
            showWizardError(`✓ Draft saved as ${data.order_number}.`, false);
        } else {
            showWizardError(data.message || 'Could not save draft.');
        }
    } catch (err) {
        showWizardError('Draft save error: ' + err.message);
    }
}

async function submitUniversalOrder() {
    const p = universalOrderWizardState.selectedProduct;
    const cust = universalOrderWizardState.selectedCustomer;

    if (!cust) {
        showWizardError('Please select a customer.');
        jumpToWizardStep(1);
        return;
    }
    if (!p) {
        showWizardError('Please select a product.');
        jumpToWizardStep(2);
        return;
    }

    const fieldsErr = validateWizardStep3Fields();
    if (fieldsErr) {
        showWizardError(fieldsErr);
        jumpToWizardStep(3);
        return;
    }

    const submitBtn = document.getElementById('wizard-btn-submit');
    if (submitBtn) submitBtn.disabled = true;

    const isClient = currentUser && currentUser.role === 'CLIENT';
    const clientId = cust.id;

    const payload = {
        service_id: p.id,
        service_name: p.name,
        client_id: clientId,
        company_email: cust.email,
        form_values: universalOrderWizardState.formValues,
        repeatable_data: universalOrderWizardState.repeatableData,
        uploaded_docs: universalOrderWizardState.uploadedDocs,
        notes: document.getElementById('wizard-order-notes')?.value || '',
        order_id: universalOrderWizardState.draftOrderId
    };

    try {
        const endpoint = isClient ? '/api/client/orders' : '/api/admin/orders';
        const res = await fetch(endpoint, {
            method: 'POST',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const data = await res.json().catch(() => ({}));
        if (res.ok && data.status === 'success') {
            closeUniversalOrderWizard();
            alert(`🎉 Order ${data.order?.order_number || data.order_number || 'created'} submitted successfully!`);
            if (typeof loadAdminOrders === 'function') loadAdminOrders();
            if (typeof loadClientOrders === 'function') loadClientOrders();
        } else {
            throw new Error(data.message || 'Order submission failed.');
        }
    } catch (err) {
        showWizardError(err.message);
    } finally {
        if (submitBtn) submitBtn.disabled = false;
    }
}

async function loadWizardAdminClientsSelect() {
    const select = document.getElementById('wizard-client-id');
    if (!select) return;
    select.innerHTML = '<option value="">-- Select Client --</option>';
    try {
        const res = await fetch('/api/admin/customers?limit=500', { credentials: 'same-origin' });
        const data = await res.json().catch(() => ({}));
        const customers = data.customers || data.users || [];
        if (Array.isArray(customers)) {
            customers.forEach(c => {
                const opt = document.createElement('option');
                opt.value = c.id;
                opt.textContent = `${c.full_name || 'Client'} (${c.email}) • ${c.b2b_id || 'B2B'}`;
                select.appendChild(opt);
            });
        }
    } catch (err) { console.error(err); }
}

function onWizardClientChange() {
    const select = document.getElementById('wizard-client-id');
    const emailInput = document.getElementById('wizard-owner-email');
    if (!select || !emailInput) return;

    const selectedOpt = select.options[select.selectedIndex];
    if (selectedOpt && selectedOpt.textContent) {
        const match = selectedOpt.textContent.match(/\(([^)]+)\)/);
        if (match && match[1]) {
            emailInput.value = match[1];
        }
    }
}

function showWizardError(msg, isError = true) {
    const banner = document.getElementById('wizard-error-banner');
    if (!banner) return;
    banner.style.display = 'block';
    banner.textContent = msg;
    if (isError) {
        banner.style.background = 'var(--color-danger-soft)';
        banner.style.color = 'var(--color-danger)';
        banner.style.borderColor = 'var(--color-danger-border)';
    } else {
        banner.style.background = 'var(--color-success-soft)';
        banner.style.color = 'var(--color-success)';
        banner.style.borderColor = 'var(--color-success-border)';
    }
}

function hideWizardError() {
    const banner = document.getElementById('wizard-error-banner');
    if (banner) banner.style.display = 'none';
}

function roundToTwo(num) {
    return +(Math.round(num + "e+2")  + "e-2");
}

/* ====================================================
   DEDICATED CREATE CUSTOMER ENGINE
   ==================================================== */
function openCreateCustomerModal() {
    const modal = document.getElementById('modal-create-customer');
    if (!modal) return;

    const form = document.getElementById('form-create-customer');
    if (form) form.reset();
    onCustomerTypeCardChange('Normal');
    
    const errBanner = document.getElementById('create-customer-error-banner');
    if (errBanner) errBanner.style.display = 'none';

    modal.style.display = 'flex';
    modal.classList.add('active');
    safeCreateIcons();
}

function closeCreateCustomerModal() {
    const modal = document.getElementById('modal-create-customer');
    if (modal) {
        modal.style.display = 'none';
        modal.classList.remove('active');
    }
}

function onCustomerTypeCardChange(type) {
    const cardNormal = document.getElementById('cust-type-card-normal');
    const cardB2B = document.getElementById('cust-type-card-b2b');

    if (type === 'B2B') {
        if (cardNormal) { cardNormal.style.borderColor = 'var(--color-border)'; }
        if (cardB2B) { cardB2B.style.borderColor = 'var(--color-primary)'; }
    } else {
        if (cardNormal) { cardNormal.style.borderColor = 'var(--color-primary)'; }
        if (cardB2B) { cardB2B.style.borderColor = 'var(--color-border)'; }
    }
}

async function submitCreateCustomerForm(event) {
    event.preventDefault();

    const name = document.getElementById('create-cust-name').value.trim();
    const email = document.getElementById('create-cust-email').value.trim().toLowerCase();
    const phone = document.getElementById('create-cust-phone').value.trim();
    const country = document.getElementById('create-cust-country').value.trim() || 'United Kingdom';
    const password = document.getElementById('create-cust-password').value;
    const confirmPassword = document.getElementById('create-cust-password-confirm').value;

    const custTypeRadios = document.getElementsByName('create_cust_type');
    let custType = 'Normal';
    for (let r of custTypeRadios) {
        if (r.checked) { custType = r.value; break; }
    }

    const errBanner = document.getElementById('create-customer-error-banner');
    if (password !== confirmPassword) {
        if (errBanner) {
            errBanner.textContent = 'Passwords do not match.';
            errBanner.style.display = 'block';
        }
        return;
    }

    const submitBtn = document.getElementById('btn-submit-create-cust');
    if (submitBtn) submitBtn.disabled = true;

    const payload = {
        role: 'CLIENT',
        full_name: name,
        email: email,
        phone: phone,
        country: country,
        password: password,
        confirm_password: confirmPassword,
        client_type: custType,
        is_b2b: (custType === 'B2B' ? 1 : 0)
    };

    try {
        const res = await fetch('/api/admin/staff', {
            method: 'POST',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const data = await res.json().catch(() => ({}));

        if (res.ok && data.status === 'success') {
            closeCreateCustomerModal();
            alert(`🎉 Customer account created successfully! (${custType === 'B2B' ? 'B2B Customer: ' + (data.user?.b2b_id || 'B2B Account') : 'Normal Customer'})`);
            if (typeof loadAdminCustomers === 'function') loadAdminCustomers();
        } else {
            if (errBanner) {
                errBanner.textContent = data.message || 'Could not create customer.';
                errBanner.style.display = 'block';
            }
        }
    } catch (err) {
        if (errBanner) {
            errBanner.textContent = 'Connection error: ' + err.message;
            errBanner.style.display = 'block';
        }
    } finally {
        if (submitBtn) submitBtn.disabled = false;
    }
}

/* ====================================================
   SERVICE ACCESS CREDENTIALS & PASSWORD TOGGLE HELPERS
   ==================================================== */
function toggleAccessPasswordVisibility(inputId) {
    const input = document.getElementById(inputId);
    const eyeIcon = document.getElementById(`eye-${inputId}`);
    if (!input) return;

    if (input.type === 'password') {
        input.type = 'text';
        if (eyeIcon) eyeIcon.setAttribute('data-lucide', 'eye-off');
    } else {
        input.type = 'password';
        if (eyeIcon) eyeIcon.setAttribute('data-lucide', 'eye');
    }
    safeCreateIcons();
}

function toggleDisplayPasswordVisibility(elementId) {
    const el = document.getElementById(elementId);
    const eyeIcon = document.getElementById(`eye-${elementId}`);
    if (!el) return;

    const actualPass = el.getAttribute('data-actual-password') || '';
    if (el.textContent === '••••••••') {
        el.textContent = actualPass || 'None recorded';
        if (eyeIcon) eyeIcon.setAttribute('data-lucide', 'eye-off');
    } else {
        el.textContent = '••••••••';
        if (eyeIcon) eyeIcon.setAttribute('data-lucide', 'eye');
    }
    safeCreateIcons();
}

function copyTextToClipboard(text) {
    if (!text || text === 'None recorded') return;
    navigator.clipboard.writeText(text).then(() => {
        alert('📋 Copied to clipboard: ' + text);
    }).catch(() => {
        alert('Copied: ' + text);
    });
}

function toggleStaffOrderCredentialsEdit(show = true) {
    const disp = document.getElementById('staff-order-credentials-display');
    const editor = document.getElementById('staff-order-credentials-editor');
    const btn = document.getElementById('btn-edit-credentials');
    if (!disp || !editor) return;

    if (show) {
        disp.style.display = 'none';
        editor.style.display = 'block';
        if (btn) btn.style.display = 'none';

        const dispEmail = document.getElementById('disp-access-email')?.textContent || '';
        const btnCopyPass = document.getElementById('btn-copy-pass');
        const passVal = btnCopyPass ? (btnCopyPass.getAttribute('data-pass') || '') : '';

        const editEmail = document.getElementById('edit-access-email');
        const editPass = document.getElementById('edit-access-password');
        if (editEmail) editEmail.value = (dispEmail !== 'None recorded' ? dispEmail : '');
        if (editPass) editPass.value = passVal;
    } else {
        disp.style.display = 'grid';
        editor.style.display = 'none';
        if (btn) btn.style.display = 'inline-block';
    }
}

async function saveStaffOrderCredentials() {
    if (!window.staffOrderId) return;

    const email = document.getElementById('edit-access-email').value.trim();
    const password = document.getElementById('edit-access-password').value.trim();

    try {
        const res = await fetch(`/api/admin/orders/${window.staffOrderId}`, {
            method: 'PUT',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                access_email: email,
                access_email_password: password
            })
        });
        const data = await res.json().catch(() => ({}));
        if (res.ok && data.status === 'success') {
            const dispEmail = document.getElementById('disp-access-email');
            const dispPass = document.getElementById('disp-access-password');
            const btnCopyPass = document.getElementById('btn-copy-pass');

            if (dispEmail) dispEmail.textContent = email || 'None recorded';
            if (dispPass) {
                dispPass.setAttribute('data-actual-password', password);
                dispPass.textContent = '••••••••';
            }
            if (btnCopyPass) btnCopyPass.setAttribute('data-pass', password);

            toggleStaffOrderCredentialsEdit(false);
            safeCreateIcons();
        } else {
            alert('Error saving credentials: ' + (data.message || 'Unknown error'));
        }
    } catch (err) {
        alert('Connection error: ' + err.message);
    }
}

/* ====================================================
   GOOGLE DRIVE IMPORT MODAL FOR SMART INTAKE
   ==================================================== */
function openGoogleDrivePickerModal() {
    const modal = document.getElementById('modal-google-drive-picker');
    const input = document.getElementById('gdrive-link-input');
    const err = document.getElementById('gdrive-import-error');
    if (!modal) return;
    if (input) input.value = '';
    if (err) { err.style.display = 'none'; err.textContent = ''; }
    modal.classList.add('active');
    if (window.lucide && typeof window.lucide.createIcons === 'function') window.lucide.createIcons();
}

function closeGoogleDrivePickerModal() {
    const modal = document.getElementById('modal-google-drive-picker');
    if (modal) modal.classList.remove('active');
}

async function submitGoogleDriveImport(event) {
    if (event) event.preventDefault();
    const linkInput = document.getElementById('gdrive-link-input');
    const overrideSelect = document.getElementById('gdrive-category-override');
    const errEl = document.getElementById('gdrive-import-error');
    const btn = document.getElementById('btn-gdrive-submit');
    if (errEl) { errEl.style.display = 'none'; errEl.textContent = ''; }

    const driveUrl = String(linkInput?.value || '').trim();
    if (!driveUrl) {
        if (errEl) { errEl.style.display = 'block'; errEl.textContent = 'Please enter a valid Google Drive link.'; }
        return;
    }

    const docCategory = overrideSelect?.value || '';

    if (btn) btn.disabled = true;

    try {
        const res = await fetch('/api/admin/intake/google-drive-import', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url: driveUrl, document_category: docCategory })
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data.status !== 'success') {
            throw new Error(data.message || 'Failed to import Google Drive documents.');
        }

        const importedFiles = data.imported_files || [];
        if (importedFiles.length === 0) {
            throw new Error('No compatible document files were found in the Google Drive link.');
        }

        importedFiles.forEach((item) => {
            const docType = normalizeIntakeDocumentType(docCategory || item.document_type) || '';
            intakeQueueItems.push({
                id: 'gdrive_' + Date.now() + '_' + Math.random().toString(36).substr(2, 5),
                file: null,
                file_content_base64: item.content_base64 || '',
                name: item.name || 'gdrive_document.pdf',
                relPath: item.path || item.name || 'Google Drive Document',
                size: item.size || 0,
                document_type: docType,
                category: docType || item.category || 'Google Drive Document',
                upload_status: 'UPLOADED',
                process_status: 'Imported from Google Drive — Ready for processing',
                error: null,
                source: 'GOOGLE_DRIVE'
            });
        });

        closeGoogleDrivePickerModal();
        renderIntakeFilePreview();
        alert(`Successfully imported ${importedFiles.length} item(s) from Google Drive! Click Process & Auto-File to run OCR.`);
    } catch (e) {
        if (errEl) {
            errEl.style.display = 'block';
            errEl.textContent = e.message || 'Error importing Google Drive documents.';
        }
    } finally {
        if (btn) btn.disabled = false;
    }
}

async function assignIntakeCompanyCandidate(companyNumber, companyName, companyId) {
    if (!confirm(`Are you sure you want to assign company "${companyName}" (#${companyNumber}) to this client file?`)) return;
    try {
        const clientName = document.getElementById('res-client-name')?.textContent || '';
        const res = await fetch('/api/admin/companies/import', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                company_number: companyNumber,
                company_name: companyName,
                director_name: clientName
            })
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data.status !== 'success') {
            throw new Error(data.message || 'Could not assign company candidate.');
        }

        const resCompCard = document.getElementById('res-company-card');
        const resCompName = document.getElementById('res-company-name');
        const resCompNum = document.getElementById('res-company-num');
        const resCompBadge = document.getElementById('res-company-status-badge');
        const resCompExpl = document.getElementById('res-company-explanation');

        if (resCompCard) resCompCard.style.borderTopColor = '#16a34a';
        if (resCompName) resCompName.textContent = companyName;
        if (resCompNum) resCompNum.textContent = `Company #: ${companyNumber} · Manually Confirmed by Staff`;
        if (resCompBadge) resCompBadge.innerHTML = `<span class="badge-status-approved">✓ MANUALLY CONFIRMED &amp; LINKED</span>`;
        if (resCompExpl) resCompExpl.innerHTML = `<strong>Company:</strong> ${escapeHtml(companyName)} (#${escapeHtml(companyNumber)})<br><strong>Status:</strong> Assigned to client profile by staff manual review.`;

        const candidatesEl = document.getElementById('res-company-candidates');
        if (candidatesEl) candidatesEl.innerHTML = '';

        alert(`Company "${companyName}" (#${companyNumber}) has been assigned to the client profile.`);
    } catch (e) {
        alert(e.message || 'Failed to assign company candidate.');
    }
}
