/* ====================================================
   HYPETEX LIMITED - SAAS PORTAL FRONTEND LOGIC
   ==================================================== */

let currentUser = null;
let activeView = 'login';
let activeTicketId = null;
let adminChart = null;
let lastNotifications = [];
let notificationsOpen = false;
let accountMenuOpen = false;

const ALL_USER_ROLES = ['SUPER_ADMIN', 'ADMIN', 'MANAGER', 'STAFF', 'CLIENT'];
const ROLE_RANK = { CLIENT: 0, STAFF: 1, MANAGER: 2, ADMIN: 3, SUPER_ADMIN: 4 };
const STAFF_DEPARTMENTS = [
    'New Signups',
    'Orders',
    'Documents',
    'Support',
    'Compliance',
    'Accounts',
    'General'
];

function isAdminShellUser(user) {
    if (!user) return false;
    return ['SUPER_ADMIN', 'ADMIN', 'MANAGER', 'STAFF'].includes(user.role);
}

function canViewAdminDashboard() {
    return currentUser && ['SUPER_ADMIN', 'ADMIN', 'MANAGER'].includes(currentUser.role);
}

function canViewRevenue() {
    return currentUser && ['SUPER_ADMIN', 'ADMIN'].includes(currentUser.role);
}

function hasFullModuleAccess() {
    return currentUser && ['SUPER_ADMIN', 'ADMIN', 'MANAGER'].includes(currentUser.role);
}

const VIEW_ACCESS = {
    'admin-customers': ['New Signups'],
    'admin-orders': ['Orders', 'Support'],
    'admin-services': ['Orders'],
    'admin-documents': ['Documents', 'Compliance'],
    'admin-invoices': ['Accounts']
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
    const preferred = ['admin-orders', 'admin-customers', 'admin-documents', 'admin-invoices', 'admin-tasks', 'client-profile'];
    return preferred.find(canOpenView) || 'client-profile';
}

function applyPortalAccessNav() {
    document.querySelectorAll('#admin-sidebar .nav-item, #top-menu-list-admin .top-menu-item').forEach((el) => {
        const view = el.getAttribute('data-view');
        el.style.display = canOpenView(view) ? '' : 'none';
    });
    applyAdminRevenueCards();
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
    try {
        if (window.lucide && typeof lucide.createIcons === 'function') lucide.createIcons();
    } catch (err) {
        console.warn('Icon render skipped', err);
    }
}

// Immediately lock shell state on script load
setAuthShellState(true, false);

// Initialize Application on Page Load
document.addEventListener('DOMContentLoaded', async () => {
    safeCreateIcons();
    try {
        await checkAuth();
    } catch (err) {
        console.error('Auth check error:', err);
        currentUser = null;
        showLoginView();
    }
    setupEventListeners();
    window.addEventListener('resize', () => {
        if (!isMobileNav()) closeMobileNav();
    });
});

window.addEventListener('pageshow', (event) => {
    if (!event.persisted) return;
    currentUser = null;
    setAuthShellState(true, false);
    checkAuth();
});

function setupEventListeners() {
    // Nav Click Listeners
    document.querySelectorAll('.nav-item').forEach(item => {
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
            if (action === 'status' || action === 'payment_mode') return;
            e.preventDefault();
            e.stopPropagation();
            const orderId = Number(orderAction.getAttribute('data-order-id'));
            if (!orderId) return;
            if (action === 'open') openStaffOrderWorkspace(orderId);
            if (action === 'progress') advanceOrderProgress(orderId, Number(orderAction.getAttribute('data-progress') || 0));
            if (action === 'delete') openDeleteOrderModal(orderId, orderAction.getAttribute('data-order-number'));
            return;
        }
        const orderRow = e.target.closest('#adm-orders-table-body tr[data-order-id]');
        if (orderRow && !e.target.closest('select, a, button, input, textarea')) {
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
            }
        });
    }
}

// ----------------------------------------------------
// Authentication & Production Login Flow
// ----------------------------------------------------
async function checkAuth() {
    setAuthShellState(true, false);
    try {
        const res = await fetch('/api/auth/me', { credentials: 'same-origin' });
        const data = await res.json();
        if (data.status === 'success' && data.user) {
            currentUser = data.user;
            updateUserUI();
            switchView(defaultPortalView());
            setAuthShellState(false, true);
        } else {
            currentUser = null;
            showLoginView();
        }
    } catch (err) {
        console.error('Auth check error:', err);
        currentUser = null;
        showLoginView();
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
}

function showLoginView() {
    currentUser = null;
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
    
    if (!emailInput || !passInput) return;
    const email = emailInput.value;
    const password = passInput.value;
    if (errDiv) errDiv.style.display = 'none';

    try {
        const res = await fetch('/api/auth/login', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            credentials: 'same-origin',
            body: JSON.stringify({ email, password })
        });
        const data = await res.json();
        if (data.status === 'success') {
            currentUser = data.user;
            updateUserUI();
            switchView(defaultPortalView());
            setAuthShellState(false, true);
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
    }
}

async function handleLogout() {
    currentUser = null;
    closeAccountMenu();
    closeNotificationsDropdown();
    showLoginView();
    await fetch('/api/auth/logout', { method: 'POST', credentials: 'same-origin' });
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
        closeAccountMenu();
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
        closeAccountMenu();
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
    
    // Sync top horizontal menu bar
    const topMenuBar = document.getElementById('top-horizontal-menu-bar');
    const topClientList = document.getElementById('top-menu-list-client');
    const topAdminList = document.getElementById('top-menu-list-admin');
    if (topMenuBar) {
        topMenuBar.style.display = currentUser ? 'block' : 'none';
    }
    if (topClientList) {
        topClientList.style.display = (currentUser && !isAdminShellUser(currentUser)) ? 'flex' : 'none';
    }
    if (topAdminList) {
        topAdminList.style.display = (currentUser && isAdminShellUser(currentUser)) ? 'flex' : 'none';
    }

    // Refresh notifications count
    loadNotificationsCount();
}

// ----------------------------------------------------
// View Router & Page Switching
// ----------------------------------------------------
function switchView(viewName) {
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
    activeView = viewName;
    setActiveViewPanel(viewName);
    
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
        case 'admin-services': loadAdminServices(); break;
        case 'admin-invoices': loadAdminInvoices(); break;
        case 'admin-documents': loadAdminDocuments(); break;
        case 'admin-logs': loadAdminLogs(); break;
        case 'admin-settings': loadAdminSettings(); break;
    }
    
    lucide.createIcons();
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
    const search = document.getElementById('global-search-input')?.value || '';
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
                        <a href="/api/documents/${doc.id}/download" class="btn-secondary" style="padding:4px 10px; font-size:0.75rem; text-decoration:none;" onclick="event.stopPropagation();">Download</a>
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
async function loadClientDashboard() {
    const grid = document.getElementById('dash-active-orders-grid');
    const companiesGrid = document.getElementById('dash-companies-grid');
    const errorBox = document.getElementById('dash-error');
    if (errorBox) {
        errorBox.style.display = 'none';
        errorBox.textContent = '';
    }
    if (grid) {
        grid.innerHTML = `
            <div class="order-card skeleton-card" aria-label="Loading order" style="opacity: 0.6;">
                <div class="order-card-header"><div style="width:100px; height:18px; background:#e2e8f0; border-radius:4px;"></div></div>
                <div style="height:24px; background:#e2e8f0; border-radius:4px; margin:12px 0;"></div>
                <div style="height:8px; background:#e2e8f0; border-radius:4px;"></div>
            </div>
            <div class="order-card skeleton-card" aria-label="Loading order" style="opacity: 0.6;">
                <div class="order-card-header"><div style="width:100px; height:18px; background:#e2e8f0; border-radius:4px;"></div></div>
                <div style="height:24px; background:#e2e8f0; border-radius:4px; margin:12px 0;"></div>
                <div style="height:8px; background:#e2e8f0; border-radius:4px;"></div>
            </div>
        `;
    }
    if (companiesGrid) {
        companiesGrid.innerHTML = `
            <div class="company-card skeleton-card" aria-label="Loading companies" style="opacity:0.6; min-height:88px;"></div>
            <div class="company-card skeleton-card" aria-label="Loading companies" style="opacity:0.6; min-height:88px;"></div>
        `;
    }

    try {
        const res = await fetch('/api/client/dashboard');
        if (!res.ok) throw new Error('Unable to load dashboard data. Please try again.');
        const data = await res.json();
        
        if (data.status === 'success') {
            const heroDate = document.getElementById('hero-current-date');
            const heroName = document.getElementById('hero-user-name');
            const heroComps = document.getElementById('hero-companies-count');
            const displayName = (data.user_name || currentUser?.full_name || currentUser?.email || 'there').trim();
            const todayLabel = new Date().toLocaleDateString('en-GB', {
                weekday: 'long', day: 'numeric', month: 'long', year: 'numeric'
            });
            
            if (heroDate) heroDate.textContent = todayLabel;
            if (heroName) heroName.textContent = displayName;
            if (heroComps) heroComps.textContent = `${data.total_companies ?? 0}`;
            const companiesPill = document.getElementById('dash-companies-count-pill');
            const companyCount = Number(data.total_companies ?? 0);
            if (companiesPill) companiesPill.textContent = `${companyCount} ${companyCount === 1 ? 'Company' : 'Companies'}`;
            
            document.getElementById('dash-card-portfolio-count').textContent = data.total_companies ?? 0;
            document.getElementById('dash-uk-count').textContent = `${data.uk_companies ?? 0} UK`;
            const intlEl = document.getElementById('dash-intl-count');
            if (intlEl) intlEl.textContent = `${data.intl_companies ?? 0} International`;
            
            document.getElementById('dash-card-overdue-count').textContent = data.overdue_deadlines ?? 0;
            const overdueBadge = document.getElementById('dash-overdue-badge');
            if (overdueBadge) {
                if (!data.overdue_deadlines) {
                    overdueBadge.className = 'health-badge success';
                    overdueBadge.innerHTML = `<i data-lucide="check-circle-2"></i> All on track`;
                } else {
                    overdueBadge.className = 'health-badge warning';
                    overdueBadge.innerHTML = `<i data-lucide="alert-triangle"></i> ${data.overdue_deadlines} Overdue`;
                }
            }
            
            document.getElementById('dash-card-upcoming-count').textContent = data.upcoming_deadlines ?? 0;
            const upcomingBadge = document.getElementById('dash-upcoming-badge');
            if (upcomingBadge) {
                if (!data.upcoming_deadlines) {
                    upcomingBadge.className = 'health-badge info';
                    upcomingBadge.innerHTML = `<i data-lucide="calendar"></i> No deadlines soon`;
                } else {
                    upcomingBadge.className = 'health-badge info';
                    upcomingBadge.innerHTML = `<i data-lucide="clock"></i> ${data.upcoming_deadlines} Due Soon`;
                }
            }
            
            document.getElementById('dash-active-orders-num').textContent = data.active_orders_count ?? 0;
            renderActiveOrdersGrid(data.active_orders || []);
            renderDashboardCompanies(data.companies || []);
            lucide.createIcons();
        } else {
            throw new Error('Unable to load dashboard data. Please try again.');
        }
    } catch (err) {
        console.error('Error loading dashboard:', err);
        if (errorBox) {
            errorBox.style.display = 'block';
            errorBox.textContent = 'Unable to load dashboard data. Please try again.';
        }
        if (grid) {
            grid.innerHTML = `
                <div style="grid-column: 1 / -1; text-align:center; padding:40px; background:#fff; border-radius:16px; border:1px solid #fee2e2;" role="alert">
                    <i data-lucide="alert-circle" style="width:40px; height:40px; color:#ef4444;"></i>
                    <h3 style="font-size:1.05rem; font-weight:700; color:#0f172a; margin-top:10px;">Unable to load dashboard data. Please try again.</h3>
                    <button class="btn-primary" style="margin-top:16px;" onclick="loadClientDashboard()">
                        <i data-lucide="refresh-cw"></i> Retry
                    </button>
                </div>
            `;
        }
        if (companiesGrid) {
            companiesGrid.innerHTML = `<div style="grid-column:1/-1; color:#64748b; padding:24px; text-align:center;">Unable to load companies.</div>`;
        }
        lucide.createIcons();
    }
}

function renderActiveOrdersGrid(orders) {
    const grid = document.getElementById('dash-active-orders-grid');
    if (!grid) return;
    
    if (orders.length === 0) {
        grid.innerHTML = `
            <div style="grid-column: 1 / -1; text-align:center; padding:48px; background:#fff; border-radius:16px; border:1px solid #e2e8f0;" role="status">
                <i data-lucide="check-circle-2" style="width:48px; height:48px; color:#10b981;"></i>
                <h3 style="font-size:1.1rem; font-weight:700; color:#0f172a; margin-top:12px;">No active orders</h3>
                <p style="color:#64748b; font-size:0.85rem; margin-top:4px;">Completed orders can be viewed in your order history.</p>
                <button class="btn-secondary" style="margin-top:16px;" onclick="switchView('client-orders')">View Order History</button>
            </div>
        `;
        lucide.createIcons();
        return;
    }
    
    grid.innerHTML = orders.map(order => {
        const isCompleted = order.status === 'Completed';
        const statusClass = String(order.status || '').toLowerCase().replace(/\s+/g, '-');
        const serviceCount = Number(order.total_items_count || (order.service_items || []).length || 1);
        const servicesLabel = `${serviceCount} service${serviceCount === 1 ? '' : 's'}`;
        const stages = Array.isArray(order.stages) ? order.stages : [];
        const items = Array.isArray(order.service_items) ? order.service_items : [];
        
        const stagesHtml = stages.map((stg, i) => {
            const iconSymbol = stg.status === 'completed' ? '✓' : (i + 1).toString();
            return `
                <div class="timeline-stage-item ${escapeHtml(stg.status || 'pending')}">
                    <div class="stage-icon-circle">${iconSymbol}</div>
                    <div class="stage-label-text">${escapeHtml(stg.name || '')}</div>
                </div>
            `;
        }).join('');
        
        const servicesRowsHtml = items.map(s => {
            const badgeClass = s.status === 'Done' || s.status === 'Completed' ? 'completed' : (s.status === 'In Progress' || s.status === 'Processing' ? 'processing' : 'pending');
            const label = s.category ? `${s.category} · ${s.name}` : s.name;
            return `
                <tr>
                    <td>${escapeHtml(label || '')}</td>
                    <td style="text-align:right;"><span class="status-badge ${badgeClass}">${escapeHtml(s.status || '')}</span></td>
                </tr>
            `;
        }).join('');
        
        return `
            <div class="order-card order-card-clickable tracker-order-card" role="button" tabindex="0" onclick="openOrderDetailsModal(${order.id})" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();openOrderDetailsModal(${order.id});}">
                <div class="order-card-header">
                    <div>
                        <div class="order-number">${escapeHtml(order.order_number)}</div>
                        <div class="order-date">${servicesLabel}</div>
                    </div>
                    <span class="status-badge ${statusClass}">${escapeHtml(order.status || '')}</span>
                </div>

                <div class="order-progress-section">
                    <div class="progress-labels">
                        <span>${escapeHtml(order.service_name || '')}</span>
                        <span>${Number(order.progress_percent || 0)}%</span>
                    </div>
                    <div class="progress-track">
                        <div class="progress-bar ${isCompleted ? 'completed' : ''}" style="width: ${Number(order.progress_percent || 0)}%;"></div>
                    </div>
                </div>

                <div class="card-horizontal-timeline">
                    ${stagesHtml}
                </div>

                <table class="card-services-table">
                    <thead>
                        <tr>
                            <th>SERVICE</th>
                            <th style="text-align:right;">STATUS</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${servicesRowsHtml}
                    </tbody>
                </table>
            </div>
        `;
    }).join('');
}

function companyCountryLabel(company) {
    if (company && company.country) return company.country;
    const office = String(company && company.reg_office ? company.reg_office : '');
    if (/united kingdom|london|uk\b/i.test(office)) return 'United Kingdom';
    if (office) return office.split(',').pop().trim() || '—';
    return '—';
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
    const items = Array.isArray(company && company.deadlines) ? company.deadlines : [];
    if (!items.length) return 'No upcoming deadlines';
    return items.map((item) => `${item.label}: ${formatUkNumericDate(item.date)}`).join(' · ');
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
    return Boolean(
        doc
        && (
            doc.is_customer_upload
            || doc.uploaded_by === 'Customer Upload'
            || doc.category === 'Checkout Upload'
        )
    );
}

function countryBadgeHtml(company) {
    if (isUkCompany(company)) {
        return `<span class="portfolio-badge-uk"><span aria-hidden="true">🇬🇧</span> UK</span>`;
    }
    const label = companyCountryLabel(company);
    return `<span class="portfolio-badge-intl">${escapeHtml(label === '—' ? 'INTL' : label)}</span>`;
}

let clientCompaniesCache = [];
let adminCompaniesCache = [];
let portfolioDetailCache = null;
let activePortfolioDocumentsTab = 'customer';
let adminCompanyClientsCache = [];

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

function renderPortfolioCompanies(companies, targetGridId = 'portfolio-companies-grid', targetCountId = 'portfolio-company-count', pending = []) {
    const grid = document.getElementById(targetGridId);
    const countPill = document.getElementById(targetCountId);
    const list = Array.isArray(companies) ? companies : [];
    const waiting = Array.isArray(pending) ? pending : [];
    const isAdmin = targetGridId === 'admin-portfolio-companies-grid';
    if (countPill) countPill.textContent = `${list.length} UK`;
    if (!grid) return;
    if (!list.length && !waiting.length) {
        grid.innerHTML = `
            <div class="portfolio-empty" role="status">
                <i data-lucide="building-2" style="width:40px; height:40px; color:var(--brand-secondary);"></i>
                <h3>No companies in your Business Portfolio yet.</h3>
                <p>A card appears here after a Digital, Professional, or All Inclusive company registration is ordered on brixenconsultants.com and the company name is sent from WordPress.</p>
            </div>
        `;
        if (window.lucide) lucide.createIcons();
        return;
    }
    const registeredHtml = list.map((company) => {
        const active = isCompanyActive(company);
        const companyId = Number(company.id);
        return `
            <article class="portfolio-card" data-company-id="${companyId}" role="button" tabindex="0" onclick="openCompanyPortfolioDetail(${companyId})" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();openCompanyPortfolioDetail(${companyId});}">
                <div class="portfolio-card-head">
                    <div class="portfolio-card-icon" aria-hidden="true"><i data-lucide="building-2"></i></div>
                    <div class="portfolio-card-titles">
                        <h3>${escapeHtml(company.name || 'Company')}</h3>
                        <p>${escapeHtml(company.company_number || '—')}</p>
                        <p class="portfolio-card-address">${escapeHtml(portfolioRegisteredAddressText(company))}</p>
                    </div>
                    <div class="portfolio-card-badges">
                        ${countryBadgeHtml(company)}
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
    }).join('');
    grid.innerHTML = `${renderPendingRegistrationCards(waiting, isAdmin)}${registeredHtml}`;
    if (window.lucide) lucide.createIcons();
}

let companiesHouseSearchTimer = null;

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

function previewPortfolioDocument(docId) {
    const id = Number(docId);
    if (!id) return;
    window.open(`/api/documents/${id}/download`, '_blank', 'noopener,noreferrer');
}

function renderPortfolioDocumentButtons(documents, emptyMessage) {
    const list = Array.isArray(documents) ? documents : [];
    if (!list.length) {
        return emptyMessage ? `<p class="portfolio-related-empty">${escapeHtml(emptyMessage)}</p>` : '';
    }
    return `
        <div class="portfolio-doc-button-grid">
            ${list.map((doc) => {
                const customerUpload = isCustomerUploadedDocument(doc);
                const meta = customerUpload
                    ? `${escapeHtml(doc.status || 'Pending Review')} · ${escapeHtml(doc.file_type || doc.category || 'Upload')}`
                    : escapeHtml(doc.category || doc.file_type || 'Document');
                return `
                    <div class="portfolio-doc-button-card">
                        <div class="portfolio-doc-button-copy">
                            <strong>${escapeHtml(doc.name || 'Document')}</strong>
                            <span>${meta}</span>
                        </div>
                        <button type="button" class="portfolio-tab portfolio-doc-preview-btn" onclick="previewPortfolioDocument(${Number(doc.id)})">View</button>
                    </div>
                `;
            }).join('')}
        </div>
    `;
}

function setPortfolioText(id, value) {
    const el = document.getElementById(id);
    if (el) el.textContent = value || '—';
}

function fillCompanyPortfolioModal(payload) {
    portfolioDetailCache = payload || {};
    const company = payload.company || {};
    const office = payload.registered_office || {};
    const uk = isUkCompany(company);
    setPortfolioText('portfolio-detail-name', company.name || 'Company');
    setPortfolioText('portfolio-detail-number', company.company_number || '');
    setPortfolioText('portfolio-detail-address', portfolioRegisteredAddressText(company));
    setPortfolioText('portfolio-detail-name-field', company.name || '—');
    setPortfolioText('portfolio-detail-number-field', company.company_number || '—');
    setPortfolioText('portfolio-detail-status', companyStatusLabel(company));
    setPortfolioText('portfolio-detail-country', companyCountryLabel(company));
    setPortfolioText('portfolio-detail-inc', formatUkNumericDate(company.inc_date));
    setPortfolioText('portfolio-detail-director', company.director || '—');
    const officeAddress = office.address || portfolioRegisteredAddressText(company);
    setPortfolioText('portfolio-detail-office', officeAddress === '——————' ? '—' : (officeAddress || '—'));
    setPortfolioText('portfolio-detail-postcode', office.postcode || '—');
    const countryBadge = document.getElementById('portfolio-detail-country-badge');
    if (countryBadge) {
        countryBadge.className = uk ? 'portfolio-badge-uk' : 'portfolio-badge-intl';
        countryBadge.textContent = uk ? '🇬🇧 UK' : (companyCountryLabel(company) || 'INTL');
    }
    const statusBadge = document.getElementById('portfolio-detail-status-badge');
    if (statusBadge) {
        statusBadge.className = `portfolio-badge-status${isCompanyActive(company) ? '' : ' is-pending'}`;
        statusBadge.textContent = companyStatusLabel(company);
    }
    const activity = document.getElementById('portfolio-detail-activity');
    if (activity) activity.textContent = 'No SIC or business activity details on file.';

    const deadlines = Array.isArray(company.deadlines) ? company.deadlines : [];
    const complianceEl = document.getElementById('portfolio-detail-compliance');
    if (complianceEl) {
        const canEditCompliance = isAdminShellUser(currentUser);
        const utrValue = company.utr_number || '';
        const authValue = company.authentication_code || '';
        const activationValue = company.activation_code || '';
        const fieldHtml = canEditCompliance ? `
            <form class="portfolio-compliance-form" onsubmit="saveCompanyCompliance(event)">
                <p class="portfolio-empty-copy">Stored on this company card for filings and HMRC / Companies House work.</p>
                <div class="portfolio-detail-grid">
                    <div>
                        <label for="portfolio-compliance-utr">UTR number</label>
                        <input type="text" id="portfolio-compliance-utr" name="utr_number" class="select-filter" maxlength="15" autocomplete="off" value="${escapeHtml(utrValue)}" placeholder="e.g. 1234567890">
                    </div>
                    <div>
                        <label for="portfolio-compliance-auth">Authentication code</label>
                        <input type="text" id="portfolio-compliance-auth" name="authentication_code" class="select-filter" maxlength="12" autocomplete="off" value="${escapeHtml(authValue)}" placeholder="Companies House auth code">
                    </div>
                    <div class="full">
                        <label for="portfolio-compliance-activation">Activation code</label>
                        <input type="text" id="portfolio-compliance-activation" name="activation_code" class="select-filter" maxlength="40" autocomplete="off" value="${escapeHtml(activationValue)}" placeholder="WebFiling activation code">
                    </div>
                </div>
                <div class="portfolio-compliance-actions">
                    <button type="submit" class="btn-primary" id="portfolio-compliance-save">Save codes</button>
                    <span class="portfolio-compliance-status" id="portfolio-compliance-status" role="status"></span>
                </div>
            </form>
        ` : `
            <div class="portfolio-detail-grid">
                <div><span>UTR number</span><strong>${escapeHtml(utrValue || '—')}</strong></div>
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
            ? `<div class="portfolio-related-list">${rows.join('')}</div>`
            : `<p class="portfolio-related-empty">No upcoming deadlines</p>`;
        complianceEl.innerHTML = `${fieldHtml}${deadlineHtml}`;
    }

    const orders = Array.isArray(payload.orders) ? payload.orders : [];
    const ordersEl = document.getElementById('portfolio-detail-orders');
    if (ordersEl) {
        if (!orders.length) {
            ordersEl.innerHTML = `<p class="portfolio-related-empty">No orders linked to this company.</p>`;
        } else {
            ordersEl.innerHTML = `<div class="portfolio-related-list">${orders.map((order) => `
                <div class="portfolio-related-row">
                    <div>
                        <strong>${escapeHtml(order.order_number || 'Order')}</strong>
                        <span>${escapeHtml(order.service_name || '')} · ${escapeHtml(order.status || '')}</span>
                    </div>
                    <button type="button" class="btn-secondary" style="padding:4px 10px; font-size:0.75rem;" onclick="openPortfolioCompanyOrder(${Number(order.id)})">View</button>
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
                        <option value="Company Documents">Company Documents</option>
                    </select>
                </div>
                <input type="file" name="file" required accept=".pdf,.png,.jpg,.jpeg,.doc,.docx,.zip">
                <textarea name="client_message" rows="2" placeholder="Optional note for the customer"></textarea>
                <div class="portfolio-doc-upload-actions">
                    <button type="submit" class="btn-primary">Upload and notify customer</button>
                    <span class="portfolio-doc-upload-status" role="status"></span>
                </div>
            </form>
        ` : '';
        docsEl.innerHTML = `
            <div class="portfolio-tabs portfolio-doc-tabs" role="tablist" aria-label="Company documents">
                <button type="button" class="portfolio-tab${activePortfolioDocumentsTab === 'customer' ? ' active' : ''}" data-doc-tab="customer" onclick="switchPortfolioDocumentsTab('customer')">Customer uploads</button>
                <button type="button" class="portfolio-tab${activePortfolioDocumentsTab === 'posted' ? ' active' : ''}" data-doc-tab="posted" onclick="switchPortfolioDocumentsTab('posted')">Posted documents</button>
            </div>
            <div class="portfolio-doc-panel" data-doc-panel="customer"${activePortfolioDocumentsTab === 'customer' ? '' : ' hidden'}>
                ${renderPortfolioDocumentButtons(customerDocs, '')}
            </div>
            <div class="portfolio-doc-panel" data-doc-panel="posted"${activePortfolioDocumentsTab === 'posted' ? '' : ' hidden'}>
                ${renderPortfolioDocumentButtons(otherDocs, 'No posted or staff documents for this company yet.')}
            </div>
            ${uploadHtml}
        `;
    }

    const tickets = Array.isArray(payload.tickets) ? payload.tickets : [];
    const supportEl = document.getElementById('portfolio-detail-support');
    if (supportEl) {
        const ticketRows = tickets.length ? tickets.map((ticket) => `
            <div class="portfolio-related-row">
                <div>
                    <strong>${escapeHtml(ticket.ticket_number || 'Ticket')}</strong>
                    <span>${escapeHtml(ticket.subject || '')} · ${escapeHtml(ticket.status || '')}</span>
                </div>
                <button type="button" class="btn-secondary" style="padding:4px 10px; font-size:0.75rem;" onclick="openPortfolioSupportTicket(${Number(ticket.id)})">Open</button>
            </div>
        `).join('') : `<p class="portfolio-related-empty">No support tickets for this company.</p>`;
        supportEl.innerHTML = `
            <div class="portfolio-related-list">${ticketRows}</div>
            <div style="margin-top:12px;">
                <button type="button" class="btn-primary" onclick="openPortfolioCompanySupport()">Contact Support</button>
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
    const cached = clientCompaniesCache.find((item) => Number(item.id) === requestedId);
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

function openPortfolioSupportTicket(ticketId) {
    const tickets = (portfolioDetailCache && portfolioDetailCache.tickets) || [];
    const ticket = tickets.find((item) => Number(item.id) === Number(ticketId)) || {};
    closeCompanyPortfolioDetail();
    openChatModal(ticketId, ticket.subject || 'Support', ticket.ticket_number || '');
}

function openPortfolioCompanySupport() {
    closeCompanyPortfolioDetail();
    switchView('client-support');
}

function renderDashboardCompanies(companies) {
    const grid = document.getElementById('dash-companies-grid');
    if (!grid) return;
    const list = Array.isArray(companies) ? companies : [];
    if (!list.length) {
        grid.innerHTML = `
            <div style="grid-column: 1 / -1; text-align:center; padding:36px; background:#fff; border-radius:16px; border:1px solid #e2e8f0;" role="status">
                <i data-lucide="building-2" style="width:40px; height:40px; color:#94a3b8;"></i>
                <h3 style="font-size:1.05rem; font-weight:700; color:#0f172a; margin-top:10px;">No companies found</h3>
                <p style="color:#64748b; font-size:0.85rem; margin-top:4px;">Registered companies linked to your account will appear here.</p>
            </div>
        `;
        return;
    }
    grid.innerHTML = list.map((company) => `
        <button type="button" class="company-card" onclick="switchView('client-companies')">
            <div class="company-card-name">${escapeHtml(company.name || 'Company')}</div>
            <div class="company-card-meta">${escapeHtml(company.company_number || '—')}</div>
            <div class="company-card-footer">
                <span>${escapeHtml(companyCountryLabel(company))}</span>
                <span class="status-badge ${(company.status || company.account_status) === 'Active' || (company.account_status === 'Good Standing') ? 'completed' : 'pending'}">${escapeHtml(company.status || company.account_status || '—')}</span>
            </div>
        </button>
    `).join('');
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
        renderPortfolioCompanies(clientCompaniesCache, 'portfolio-companies-grid', 'portfolio-company-count', data.pending_registrations || []);
    } catch (err) {
        clientCompaniesCache = [];
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
        const list = data.companies || [];
        if (countPill) countPill.textContent = `${data.total_uk || list.length} UK`;
        const chBanner = document.getElementById('admin-ch-banner');
        if (chBanner) chBanner.hidden = Boolean(data.companies_house_configured);
        adminCompanyClientsCache = Array.isArray(data.clients) ? data.clients : [];
        adminCompaniesCache = list;
        renderPortfolioCompanies(list, 'admin-portfolio-companies-grid', 'admin-portfolio-company-count', data.pending_registrations || []);
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
                    <td><span class="status-badge ${i.status.toLowerCase()}">${i.status}</span></td>
                    <td><button class="btn-secondary" style="padding:4px 10px; font-size:0.75rem;">Download PDF</button></td>
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
                <div class="modal-card" style="margin:0; display:flex; flex-direction:column; justify-space-between;">
                    <div>
                        <div style="display:flex; justify-content:space-between; align-items:center;">
                            <span class="badge" style="background:#e0f2fe; color:#0369a1; font-weight:700; font-size:0.75rem;">${s.category}</span>
                            <span style="font-size:1.25rem; font-weight:800; color:#003971;">£${parseFloat(s.price).toFixed(2)}</span>
                        </div>
                        <h3 style="font-size:1.1rem; font-weight:700; margin:12px 0 6px 0; color:#0f172a;">${s.name}</h3>
                        <p style="font-size:0.85rem; color:#64748b; margin-bottom:16px;">${s.description}</p>
                    </div>
                    <button class="btn-primary" style="width:100%; justify-content:center;">Order Service</button>
                </div>
            `).join('');
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
                        <a href="/api/documents/${d.id}/download" target="_blank" class="btn-secondary" style="padding:4px 10px; font-size:0.75rem; text-decoration:none; margin-right:6px;">View</a>
                        <a href="/api/documents/${d.id}/download" class="btn-primary" style="padding:4px 10px; font-size:0.75rem; text-decoration:none;">Download</a>
                    </td>
                </tr>
            `).join('');
        }
    } catch (err) { console.error(err); }
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
        setText('adm-tot-rev', data.stats.total_revenue);
        setText('adm-tot-tix', data.stats.open_tickets);
        renderAdminChart((data.charts && data.charts.monthly) || []);
    } catch (err) {
        console.error(err);
    }
}

function drawRevenueFallback(canvas, labels, values) {
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
    const max = Math.max(...values, 1);
    ctx.strokeStyle = '#e2e8f0';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(pad.left, pad.top);
    ctx.lineTo(pad.left, pad.top + plotH);
    ctx.lineTo(pad.left + plotW, pad.top + plotH);
    ctx.stroke();
    ctx.fillStyle = '#94a3b8';
    ctx.font = '11px Inter, sans-serif';
    ctx.textAlign = 'right';
    ctx.fillText(`£${Math.round(max)}`, pad.left - 8, pad.top + 4);
    ctx.fillText('£0', pad.left - 8, pad.top + plotH);
    const points = values.map((value, idx) => {
        const x = pad.left + (values.length === 1 ? plotW / 2 : (plotW * idx) / (values.length - 1));
        const y = pad.top + plotH - (value / max) * plotH;
        return { x, y, label: labels[idx] || '' };
    });
    ctx.beginPath();
    points.forEach((pt, idx) => {
        if (idx === 0) ctx.moveTo(pt.x, pt.y);
        else ctx.lineTo(pt.x, pt.y);
    });
    ctx.strokeStyle = '#003971';
    ctx.lineWidth = 2;
    ctx.stroke();
    if (points.length) {
        ctx.lineTo(points[points.length - 1].x, pad.top + plotH);
        ctx.lineTo(points[0].x, pad.top + plotH);
        ctx.closePath();
        ctx.fillStyle = 'rgba(0, 108, 255, 0.12)';
        ctx.fill();
    }
    points.forEach((pt) => {
        ctx.beginPath();
        ctx.arc(pt.x, pt.y, 4, 0, Math.PI * 2);
        ctx.fillStyle = '#006cff';
        ctx.fill();
        ctx.strokeStyle = '#ffffff';
        ctx.lineWidth = 2;
        ctx.stroke();
        ctx.fillStyle = '#64748b';
        ctx.font = '10px Inter, sans-serif';
        ctx.textAlign = 'center';
        ctx.fillText(pt.label, pt.x, height - 10);
    });
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
                        fill: true,
                        tension: 0.35,
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
                    plugins: {
                        legend: { display: false },
                        tooltip: {
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
                                callback(value) {
                                    return `£${value}`;
                                }
                            }
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
    if (fromEl) fromEl.classList.toggle('is-visible', showCustom);
    if (toEl) toEl.classList.toggle('is-visible', showCustom);
    if (!showCustom) {
        if (fromEl) fromEl.value = '';
        if (toEl) toEl.value = '';
    }
}

function adminOrderProgressRange() {
    const raw = document.getElementById('filter-admin-order-progress')?.value || '';
    if (!raw || !raw.includes('-')) return { min: '', max: '' };
    const [min, max] = raw.split('-');
    return { min, max };
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
    if (payment) qs.set('payment_status', payment);
    if (customer) qs.set('customer_id', customer);
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

async function loadAdminOrders() {
    const tbody = document.getElementById('adm-orders-table-body');
    showAdminOrdersError('');
    try {
        if (tbody) {
            tbody.innerHTML = `<tr><td colspan="9" style="text-align:center; color:#64748b; padding:28px;">Loading orders…</td></tr>`;
        }
        const qs = buildAdminOrderQuery(adminOrdersPage);
        const res = await fetch(`/api/admin/orders?${qs.toString()}`, { credentials: 'same-origin' });
        const data = await res.json();
        if (!res.ok || data.status !== 'success') {
            throw new Error(data.message || 'Unable to load orders.');
        }
        fillAdminOrderFacet('filter-admin-order-product', data.facets?.products || []);
        fillAdminOrderFacet('filter-admin-order-category', data.facets?.categories || []);
        fillAdminOrderFacet('filter-admin-order-year', data.facets?.years || []);
        fillAdminOrderFacet('filter-admin-order-customer', data.facets?.customers || [], 'full_name', 'id');
        renderAdminOrderStats(data.stats);
        renderAdminOrderPagination(data.pagination);
        if (!tbody) return;
        if (!data.orders.length) {
            tbody.innerHTML = `<tr><td colspan="10" style="text-align:center; color:#64748b; padding:28px;">No orders match the current filters.</td></tr>`;
            syncAdminOrdersSelectAllState();
            return;
        }
        tbody.innerHTML = data.orders.map(o => {
            const product = o.products_summary || o.service_name || '—';
            const category = o.category_name ? `${escapeHtml(o.category_name)} · ` : '';
            return `
                <tr class="order-row" data-order-id="${o.id}">
                    <td class="cell-select">
                        <input type="checkbox" class="adm-order-select" value="${o.id}" data-order-id="${o.id}" aria-label="Select order ${escapeHtml(o.order_number)}">
                    </td>
                    <td><button type="button" class="order-number-link" data-order-action="open" data-order-id="${o.id}">${escapeHtml(o.order_number)}</button></td>
                    <td class="cell-date">${escapeHtml(formatShortDate(o.created_at))}</td>
                    <td class="cell-client">${escapeHtml(o.client_name || '')}<div class="cell-subtext">${escapeHtml(o.client_email || '')}</div></td>
                    <td class="cell-company">${escapeHtml(o.company_name || '—')}</td>
                    <td class="cell-product">${category}${escapeHtml(product)}</td>
                    <td class="cell-price">£${parseFloat(o.total || 0).toFixed(2)}</td>
                    <td>
                        <select class="table-select" data-order-action="payment_mode" data-order-id="${o.id}">
                            <option value="" ${!o.payment_mode ? 'selected' : ''}>Select mode</option>
                            <option value="PKR(Bank Transfer)" ${o.payment_mode === 'PKR(Bank Transfer)' ? 'selected' : ''}>PKR(Bank Transfer)</option>
                            <option value="GBP(Bank Transfer)" ${o.payment_mode === 'GBP(Bank Transfer)' ? 'selected' : ''}>GBP(Bank Transfer)</option>
                            <option value="Website Charge" ${o.payment_mode === 'Website Charge' ? 'selected' : ''}>Website Charge</option>
                        </select>
                    </td>
                    <td>
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
                        <div class="order-row-actions">
                            <button type="button" class="btn-primary btn-table" data-order-action="open" data-order-id="${o.id}">Manage</button>
                            ${canDeleteOrders() ? `<button type="button" class="portfolio-delete-btn" data-order-action="delete" data-order-id="${o.id}" data-order-number="${escapeHtml(o.order_number)}" title="Delete order" aria-label="Delete order"><i data-lucide="trash-2"></i></button>` : ''}
                        </div>
                    </td>
                </tr>`;
        }).join('');
        syncAdminOrdersSelectAllState();
    } catch (err) {
        showAdminOrdersError(err.message || 'Unable to load orders.');
        if (tbody) {
            tbody.innerHTML = `<tr><td colspan="10" style="text-align:center; color:#dc2626; padding:28px;">Unable to load orders.</td></tr>`;
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
        alert('Select one order at a time to edit, or open Manage on that row.');
        return;
    }
    openStaffOrderWorkspace(ids[0]);
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

async function updateAdminOrderPaymentMode(orderId, paymentMode) {
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

function canOperateOrderDocuments() {
    return currentUser && ['SUPER_ADMIN', 'ADMIN', 'MANAGER', 'STAFF'].includes(currentUser.role);
}

function canManageOrders() {
    return canOperateOrderDocuments();
}

function canDeleteOrders() {
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
    const modes = ['', 'PKR(Bank Transfer)', 'GBP(Bank Transfer)', 'Website Charge'];
    const labels = { '': 'Select mode', 'PKR(Bank Transfer)': 'PKR(Bank Transfer)', 'GBP(Bank Transfer)': 'GBP(Bank Transfer)', 'Website Charge': 'Website Charge' };
    return modes.map((mode) => `<option value="${mode}"${mode === (selected || '') ? ' selected' : ''}>${labels[mode]}</option>`).join('');
}

function staffOrderStatusOptions(selected) {
    return ['Pending Verification', 'Pending', 'Processing', 'In Progress', 'Completed', 'Cancelled', 'Refunded'].map((st) =>
        `<option value="${st}"${st === selected ? ' selected' : ''}>${st}</option>`
    ).join('');
}

async function openStaffOrderWorkspace(orderId) {
    if (!currentUser || currentUser.role === 'CLIENT') {
        alert('Staff sign-in is required to process an order.');
        return;
    }
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
        document.getElementById('staff-order-number').textContent = order.order_number;
        document.getElementById('staff-order-meta').textContent = `${formatDateTime(order.created_at) || formatDate(order.created_at)} · £${parseFloat(order.total).toFixed(2)}`;
        setPortfolioText('staff-order-date', formatShortDate(order.created_at) || formatDate(order.created_at));
        setPortfolioText('staff-order-company', order.company_name || '—');
        const category = order.category_name ? `${order.category_name} · ` : '';
        const productSummary = order.products_summary || order.service_name || '—';
        setPortfolioText('staff-order-product', `${category}${productSummary}`.trim() || '—');
        const priceBits = [`£${parseFloat(order.total || 0).toFixed(2)}`];
        if (order.price != null && order.vat != null) {
            priceBits.push(`(£${parseFloat(order.price || 0).toFixed(2)} + £${parseFloat(order.vat || 0).toFixed(2)} VAT)`);
        }
        setPortfolioText('staff-order-price', priceBits.join(' '));
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
        const checkoutFields = order.checkout_form || [];
        const checkoutEl = document.getElementById('staff-order-checkout-form');
        if (checkoutEl) {
            checkoutEl.innerHTML = checkoutFields.length ? checkoutFields.map((field) => {
                const wide = /address|description|activity|source of funds/i.test(field.label || '');
                return `<div class="${wide ? 'full' : ''}"><span class="staff-order-field-label">${escapeHtml(field.label || '')}</span><strong>${escapeHtml(field.value || '—')}</strong></div>`;
            }).join('') : '<p class="staff-order-section-copy full">No checkout form data stored for this order yet.</p>';
        }
        const lines = data.line_items || [];
        document.getElementById('staff-order-products').innerHTML = lines.length ? `<table class="data-table"><thead><tr><th>Category</th><th>Product</th><th>SKU</th><th>Qty</th><th>Unit</th><th>Total</th></tr></thead><tbody>${lines.map((item) => `
            <tr>
                <td>${escapeHtml(item.category_name || '—')}</td>
                <td>${escapeHtml(item.product_name)}</td>
                <td>${escapeHtml(item.sku || '—')}</td>
                <td>${item.quantity}</td>
                <td>£${parseFloat(item.unit_price || 0).toFixed(2)}</td>
                <td>£${parseFloat(item.line_total || 0).toFixed(2)}</td>
            </tr>`).join('')}</tbody></table>` : `<p class="staff-order-section-copy">${escapeHtml(productSummary)} · £${parseFloat(order.price || order.total || 0).toFixed(2)}</p>`;
        const docs = (data.documents || []).filter((d) => d.is_customer_upload || d.uploaded_by === 'Customer Upload' || d.category === 'Checkout Upload');
        document.getElementById('staff-order-documents').innerHTML = docs.length ? docs.map((d) => `
            <div class="staff-order-doc-row">
                <div>
                    <strong>${escapeHtml(d.name)}</strong>
                    <div class="staff-order-field-sub">Customer checkout upload · ${escapeHtml(d.status || 'Pending Review')} · ${escapeHtml(d.file_type || '')}</div>
                </div>
                <a class="btn-secondary staff-order-doc-view" href="/api/documents/${d.id}/download">View</a>
            </div>`).join('') : '';
        const modal = document.getElementById('modal-staff-order');
        if (modal) modal.classList.add('active');
        safeCreateIcons();
    } catch (err) {
        alert(err.message || 'Unable to open order.');
    }
}

function closeStaffOrderModal() {
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

function escapeHtml(value) {
    return String(value == null ? '' : value)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
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
    fillCreateUserDepartments([]);
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
        tbody.innerHTML = `<tr><td colspan="8" style="text-align:center; color:#64748b; padding:28px;">No customer signups match the current filters.</td></tr>`;
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
                <td style="font-weight:700;">${escapeHtml(c.full_name || '')}${isNew ? ' <span class="signup-new-badge">New</span>' : ''}</td>
                <td>${escapeHtml(c.email || '')}</td>
                <td>${escapeHtml(formatDateTime(c.created_at) || formatDate(c.created_at) || '—')}</td>
                <td>${escapeHtml(sourceLabel)}</td>
                <td>${c.companies_count ?? 0}</td>
                <td>${c.orders_count ?? 0}</td>
                <td><span class="status-badge ${portalReady ? 'completed' : 'pending'}">${portalLabel}</span></td>
                <td>
                    <button type="button" class="btn-primary" style="padding:4px 10px; font-size:0.75rem;" onclick="openCrmClientModal(${c.id})">View CRM Profile</button>
                    ${portalReady ? '' : `<button type="button" class="btn-secondary" style="padding:4px 10px; font-size:0.75rem; margin-left:6px;" onclick="setClientPortalPassword(${c.id}, ${JSON.stringify(c.email || '')})">Set password</button>`}
                </td>
            </tr>
        `;
    }).join('');
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
        const res = await fetch(`/api/admin/clients/${clientId}/full`);
        const data = await res.json();
        if (data.status === 'success') {
            currentCrmClientData = data;
            const c = data.client;
            
            document.getElementById('crm-modal-name').textContent = c.full_name;
            document.getElementById('crm-modal-meta').textContent = `WP User ID: ${c.wordpress_user_id || 'N/A'} | ${c.email}`;
            document.getElementById('crm-modal-avatar').textContent = c.full_name.split(' ').map(n => n[0]).join('');
            
            document.getElementById('crm-ov-email').textContent = c.email;
            document.getElementById('crm-ov-phone').textContent = c.phone || 'N/A';
            document.getElementById('crm-ov-country').textContent = c.country || 'United Kingdom';
            document.getElementById('crm-ov-status').textContent = c.status;
            document.getElementById('crm-ov-synced').textContent = c.last_synced_at ? `Synced (${c.last_synced_at})` : 'Not synced';
            document.getElementById('crm-ov-address').textContent = c.address || 'N/A';
            
            // Populate Companies tab
            const compBox = document.getElementById('crm-companies-list');
            compBox.innerHTML = data.companies.length ? data.companies.map(comp => `
                <div style="padding:10px; border:1px solid #e2e8f0; border-radius:8px; margin-bottom:8px;">
                    <strong>${comp.name}</strong> (#${comp.company_number}) • ${comp.package}
                    <div style="font-size:0.78rem; color:#64748b;">Office: ${comp.reg_office} | Status: ${comp.status}</div>
                </div>
            `).join('') : '<p style="color:#64748b;">No registered companies for this client.</p>';
            
            // Populate Orders tab
            const ordBox = document.getElementById('crm-orders-list');
            ordBox.innerHTML = data.orders.length ? data.orders.map(o => `
                <div style="padding:10px; border:1px solid #e2e8f0; border-radius:8px; margin-bottom:8px; display:flex; justify-content:space-between; gap:8px; align-items:center;">
                    <div>
                        <strong>${escapeHtml(o.order_number)}</strong> — ${escapeHtml(o.service_name)} (£${parseFloat(o.total).toFixed(2)})
                        <div style="font-size:0.78rem; color:#64748b;">${escapeHtml(formatDate(o.created_at))} · ${escapeHtml(o.status)} · ${o.progress_percent}%</div>
                    </div>
                    <div style="display:flex; gap:6px; flex-wrap:wrap;">
                        ${canUploadClientDocuments() ? `<button type="button" class="btn-secondary" style="padding:4px 10px; font-size:0.75rem;" onclick="openDeliverDocumentModal({orderId:${o.id}, companyId:${o.company_id || 'null'}})">Upload Document</button>` : ''}
                        <button type="button" class="btn-primary" style="padding:4px 10px; font-size:0.75rem;" onclick="closeCrmClientModal(); openStaffOrderWorkspace(${o.id})">Process</button>
                    </div>
                </div>
            `).join('') : '<p style="color:#64748b;">No order history available.</p>';
            
            // Populate Invoices tab
            const invBox = document.getElementById('crm-invoices-list');
            invBox.innerHTML = data.invoices.length ? data.invoices.map(inv => `
                <div style="padding:10px; border:1px solid #e2e8f0; border-radius:8px; margin-bottom:8px;">
                    <strong>${inv.invoice_number}</strong> — £${parseFloat(inv.total).toFixed(2)} (${inv.status})
                </div>
            `).join('') : '<p style="color:#64748b;">No invoices available.</p>';
            
            renderCrmDocumentsList(data.documents || []);
            const uploadBtn = document.getElementById('crm-upload-document-btn');
            if (uploadBtn) uploadBtn.style.display = canUploadClientDocuments() ? 'inline-flex' : 'none';
            
            // Populate Support tab
            const suppBox = document.getElementById('crm-support-list');
            suppBox.innerHTML = data.support_tickets.length ? data.support_tickets.map(t => `
                <div style="padding:10px; border:1px solid #e2e8f0; border-radius:8px; margin-bottom:8px;">
                    <strong>Ticket #${t.ticket_number}</strong>: ${t.subject} (${t.status})
                </div>
            `).join('') : '<p style="color:#64748b;">No support tickets filed.</p>';

            // Populate Activity Logs tab
            const logBox = document.getElementById('crm-logs-list');
            logBox.innerHTML = data.activity_logs.length ? data.activity_logs.map(lg => `
                <div style="padding:8px; border-bottom:1px solid #f1f5f9; font-size:0.8rem;">
                    <strong>${lg.action}</strong> • <span style="color:#64748b;">${formatDate(lg.created_at)}</span>
                    <div style="color:#475569;">${lg.details || ''}</div>
                </div>
            `).join('') : '<p style="color:#64748b;">No audit trail recorded.</p>';

            switchCrmTab('overview');
            document.getElementById('crm-client-modal').classList.add('active');
        }
    } catch (err) { console.error(err); }
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
                <a href="/api/documents/${doc.id}/download" target="_blank" class="btn-secondary" style="padding:4px 10px; font-size:0.75rem; text-decoration:none;">View</a>
                <a href="/api/documents/${doc.id}/download" class="btn-primary" style="padding:4px 10px; font-size:0.75rem; text-decoration:none;">Download</a>
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
    const lines = ['Document uploaded successfully.'];
    lines.push('✓ Document uploaded');
    if (data.notification_created) lines.push('✓ Client notification created');
    if (data.email_sent) {
        lines.push('✓ Email notification sent');
    } else if (data.email_status) {
        lines.push(`Email notification logged (${data.email_status}).`);
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
            file_content_base64: b64
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

async function loadAdminDocuments() {
    const tbody = document.getElementById('adm-documents-table-body');
    const errBox = document.getElementById('adm-documents-error');
    if (errBox) {
        errBox.style.display = 'none';
        errBox.textContent = '';
    }
    if (tbody) {
        tbody.innerHTML = `<tr><td colspan="6" style="text-align:center; color:#64748b; padding:28px;">Loading documents…</td></tr>`;
    }
    try {
        const res = await fetch('/api/admin/documents', { credentials: 'same-origin' });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data.status !== 'success') {
            throw new Error('Unable to load documents.');
        }
        const docs = Array.isArray(data.documents) ? data.documents : [];
        if (!tbody) return;
        if (!docs.length) {
            tbody.innerHTML = `<tr><td colspan="6" style="text-align:center; padding:36px 20px;">
                <div style="font-weight:800; color:#0f172a; margin-bottom:8px;">No files uploaded yet</div>
                <div style="color:#64748b; font-size:0.85rem; max-width:440px; margin:0 auto 16px;">Website order products such as Apostilled Documents Service are not files. Upload a PDF or image here to see it in this list.</div>
                <button type="button" class="btn-primary" onclick="openDeliverDocumentModalFromReview()">Upload Document</button>
            </td></tr>`;
            return;
        }
        tbody.innerHTML = docs.map(d => {
            const pending = d.status === 'Pending Review' || d.status === 'Requires Update';
            return `
                <tr>
                    <td>${escapeHtml(d.client_name || '—')}<div style="font-size:0.72rem; color:#64748b;">${escapeHtml(d.client_email || '')}</div></td>
                    <td>${escapeHtml(d.order_number || '—')}</td>
                    <td style="font-weight:700;">${escapeHtml(d.name || 'Document')}</td>
                    <td>${escapeHtml(d.category || '—')}</td>
                    <td><span class="status-badge ${d.status === 'Approved' ? 'completed' : (d.status === 'Rejected' ? 'cancelled' : 'pending')}">${escapeHtml(d.status || '')}</span></td>
                    <td>
                        <div class="order-row-actions">
                            <a class="btn-secondary" style="padding:4px 10px; font-size:0.75rem; text-decoration:none;" href="/api/documents/${d.id}/download">View</a>
                            ${pending ? `<button type="button" class="btn-primary" style="padding:4px 10px; font-size:0.75rem;" onclick="approveDocument(${d.id})">Approve</button>` : ''}
                        </div>
                    </td>
                </tr>
            `;
        }).join('');
    } catch (err) {
        console.error(err);
        if (errBox) {
            errBox.style.display = 'block';
            errBox.textContent = 'Unable to load documents. Please try again.';
        }
        if (tbody) {
            tbody.innerHTML = `<tr><td colspan="6" style="text-align:center; color:#dc2626; padding:28px;">Unable to load documents.</td></tr>`;
        }
    }
}

async function approveDocument(docId) {
    try {
        await fetch(`/api/admin/documents/${docId}`, {
            method: 'PUT',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ status: 'Approved', review_notes: 'Verified compliance.' })
        });
        alert('Document approved and client notified.');
        loadAdminDocuments();
    } catch (err) { console.error(err); }
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
                ? `<div style="display:flex; gap:6px; flex-wrap:wrap;">
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

async function loadAdminInvoices() {
    const tbody = document.getElementById('adm-invoices-table-body');
    const errBox = document.getElementById('adm-invoices-error');
    if (errBox) {
        errBox.style.display = 'none';
        errBox.textContent = '';
    }
    if (tbody) {
        tbody.innerHTML = `<tr><td colspan="6" style="text-align:center; color:#64748b; padding:28px;">Loading invoices…</td></tr>`;
    }
    try {
        const res = await fetch('/api/admin/invoices', { credentials: 'same-origin' });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data.status !== 'success') {
            throw new Error(data.message || 'Unable to load invoices.');
        }
        const invoices = Array.isArray(data.invoices) ? data.invoices : [];
        if (!tbody) return;
        if (!invoices.length) {
            tbody.innerHTML = `<tr><td colspan="6" style="text-align:center; color:#64748b; padding:28px;">No invoices yet.</td></tr>`;
            return;
        }
        tbody.innerHTML = invoices.map((inv) => `
            <tr>
                <td style="font-weight:700;">${escapeHtml(inv.invoice_number || '—')}</td>
                <td>${escapeHtml(inv.client_name || '—')}<div style="font-size:0.72rem; color:#64748b;">${escapeHtml(inv.client_email || '')}</div></td>
                <td>${escapeHtml(inv.order_number || '—')}</td>
                <td>£${parseFloat(inv.total || 0).toFixed(2)}</td>
                <td><span class="status-badge ${inv.status === 'Paid' ? 'completed' : (inv.status === 'Overdue' ? 'cancelled' : 'pending')}">${escapeHtml(inv.status || '')}</span></td>
                <td>${escapeHtml(formatDate(inv.created_at))}</td>
            </tr>
        `).join('');
    } catch (err) {
        console.error(err);
        if (errBox) {
            errBox.style.display = 'block';
            errBox.textContent = err.message || 'Unable to load invoices.';
        }
        if (tbody) {
            tbody.innerHTML = `<tr><td colspan="6" style="text-align:center; color:#dc2626; padding:28px;">Unable to load invoices.</td></tr>`;
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
        }
    } catch (err) { console.error(err); }
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
    const currency = document.getElementById('set-currency').value;
    const order_prefix = document.getElementById('set-order-prefix').value;
    const companies_house_api_key = (document.getElementById('set-companies-house-api-key') || {}).value || '';
    const uk_formfill_pro_url = ((document.getElementById('set-uk-formfill-url') || {}).value || '').trim();
    const payload = { company_name, support_email, currency, order_prefix };
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

function formatShortDate(dateStr) {
    if (!dateStr) return '';
    const d = new Date(dateStr);
    if (Number.isNaN(d.getTime())) return '';
    const day = String(d.getDate()).padStart(2, '0');
    const month = String(d.getMonth() + 1).padStart(2, '0');
    const year = String(d.getFullYear()).slice(-2);
    return `${day}/${month}/${year}`;
}

function handleGlobalSearch(query) {
    if (activeView === 'client-orders') {
        resetClientOrdersPage();
        loadClientOrders();
    }
}

function formatDate(dateStr) {
    if (!dateStr) return '';
    const d = new Date(dateStr);
    return d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' });
}
