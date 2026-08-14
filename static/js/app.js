/* ====================================================
   HYPETEX LIMITED - SAAS PORTAL FRONTEND LOGIC
   ==================================================== */

let currentUser = null;
let activeView = 'client-orders';
let activeTicketId = null;
let adminChart = null;
let lastNotifications = [];
let notificationsOpen = false;

function isAdminShellUser(user) {
    if (!user) return false;
    return ['SUPER_ADMIN', 'ADMIN', 'MANAGER', 'STAFF'].includes(user.role);
}

// Initialize Application on Page Load
document.addEventListener('DOMContentLoaded', async () => {
    lucide.createIcons();
    await checkAuth();
    setupEventListeners();
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
    });
}

// ----------------------------------------------------
// Authentication & Production Login Flow
// ----------------------------------------------------
async function checkAuth() {
    try {
        const res = await fetch('/api/auth/me');
        const data = await res.json();
        if (data.status === 'success') {
            currentUser = data.user;
            updateUserUI();
            switchView(isAdminShellUser(currentUser) ? 'admin-dashboard' : 'client-orders');
        } else {
            showLoginView();
        }
    } catch (err) {
        console.error('Auth check error:', err);
        showLoginView();
    }
}

function showLoginView() {
    currentUser = null;
    document.querySelectorAll('.view-panel').forEach(panel => panel.style.display = 'none');
    const loginView = document.getElementById('view-login');
    if (loginView) loginView.style.display = 'block';
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
            if (isAdminShellUser(currentUser)) {
                switchView('admin-dashboard');
            } else {
                switchView('client-orders');
            }
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
    await fetch('/api/auth/logout', { method: 'POST' });
    currentUser = null;
    closeNotificationsDropdown();
    showLoginView();
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

    const adminName = document.getElementById('admin-user-name');
    const adminRole = document.getElementById('admin-user-role');
    const adminAvatar = document.getElementById('admin-user-avatar');
    if (adminName) adminName.textContent = currentUser.full_name;
    if (adminRole) adminRole.textContent = currentUser.role;
    if (adminAvatar) {
        adminAvatar.textContent = currentUser.full_name.split(' ').map(n => n[0]).join('').substring(0,2);
    }
    
    // Toggle Sidebar View (Client vs Admin)
    const clientSide = document.getElementById('client-sidebar');
    const adminSide = document.getElementById('admin-sidebar');
    
    if (isAdminShellUser(currentUser)) {
        clientSide.style.display = 'none';
        adminSide.style.display = 'flex';
    } else {
        clientSide.style.display = 'flex';
        adminSide.style.display = 'none';
    }
    
    // Refresh notifications count
    loadNotificationsCount();
}

// ----------------------------------------------------
// View Router & Page Switching
// ----------------------------------------------------
function switchView(viewName) {
    activeView = viewName;
    
    // Hide all view panels
    document.querySelectorAll('.view-panel').forEach(panel => {
        panel.style.display = 'none';
    });
    
    // Deactivate nav items
    document.querySelectorAll('.nav-item').forEach(item => {
        item.classList.remove('active');
    });
    
    // Show target view panel
    const targetPanel = document.getElementById(`view-${viewName}`);
    if (targetPanel) {
        targetPanel.style.display = 'block';
    }
    
    // Activate nav item
    const targetNavItem = document.querySelector(`.nav-item[data-view="${viewName}"]`);
    if (targetNavItem) {
        targetNavItem.classList.add('active');
    }
    
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
        case 'admin-tasks': loadAdminTasks(); break;
        case 'admin-customers': loadAdminCustomers(); break;
        case 'admin-documents': loadAdminDocuments(); break;
        case 'admin-logs': loadAdminLogs(); break;
        case 'admin-settings': loadAdminSettings(); break;
    }
    
    lucide.createIcons();
}

// ----------------------------------------------------
// CLIENT ORDERS VIEW (Matching Reference Screenshot)
// ----------------------------------------------------
async function loadClientOrders() {
    const search = document.getElementById('global-search-input')?.value || '';
    const status = document.getElementById('filter-order-status')?.value || '';
    const sort = document.getElementById('filter-order-sort')?.value || 'date_desc';
    
    try {
        const url = `/api/client/orders?search=${encodeURIComponent(search)}&status=${encodeURIComponent(status)}&sort=${encodeURIComponent(sort)}`;
        const res = await fetch(url);
        const data = await res.json();
        
        if (data.status === 'success') {
            // Update 4 Top Stat Cards Dynamically from DB
            document.getElementById('stat-total-orders').textContent = data.stats.total_orders;
            document.getElementById('stat-completed-orders').textContent = data.stats.completed;
            document.getElementById('stat-in-progress-orders').textContent = data.stats.in_progress;
            document.getElementById('stat-total-spent').textContent = data.stats.total_spent;
            
            // Render 3-Column Order Cards Grid
            renderOrderCardsGrid(data.orders);
            document.getElementById('orders-count-label').textContent = `Showing ${data.orders.length} orders`;
        }
    } catch (err) {
        console.error('Error loading orders:', err);
    }
}

function renderOrderCardsGrid(orders) {
    const container = document.getElementById('orders-cards-container');
    if (!container) return;
    
    if (orders.length === 0) {
        container.innerHTML = `
            <div style="grid-column: 1 / -1; text-align:center; padding:48px; background:#fff; border-radius:16px; border:1px solid #e2e8f0;">
                <i data-lucide="inbox" style="width:48px; height:48px; color:#94a3b8;"></i>
                <h3 style="font-size:1.1rem; font-weight:700; margin-top:12px;">No Orders Found</h3>
                <p style="color:#64748b; font-size:0.85rem;">Try adjusting your filter or search terms.</p>
            </div>
        `;
        lucide.createIcons();
        return;
    }
    
    container.innerHTML = orders.map(order => {
        const dateFormatted = formatDate(order.created_at);
        const statusClass = order.status.toLowerCase().replace(' ', '-');
        const isCompleted = order.status === 'Completed';
        
        return `
            <div class="order-card">
                <div class="order-card-header">
                    <div>
                        <div class="order-number">${order.order_number}</div>
                        <div class="order-date">${dateFormatted}</div>
                    </div>
                    <span class="status-badge ${statusClass}">${order.status}</span>
                </div>

                <div>
                    <div class="order-service-title">${order.service_name}</div>
                    <div class="order-price">£${parseFloat(order.price).toFixed(2)}</div>
                </div>

                <div class="order-progress-section">
                    <div class="progress-labels">
                        <span>Progress</span>
                        <span>${order.progress_percent}%</span>
                    </div>
                    <div class="progress-track">
                        <div class="progress-bar ${isCompleted ? 'completed' : ''}" style="width: ${order.progress_percent}%;"></div>
                    </div>
                </div>

                <div class="order-card-footer">
                    <span class="delivery-tag">${order.delivery_label || 'Standard Service'}</span>
                    <button class="btn-details" onclick="openOrderDetailsModal(${order.id})">View Details</button>
                </div>
            </div>
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
            const timeline = data.timeline;
            
            document.getElementById('modal-order-number').textContent = order.order_number;
            document.getElementById('modal-order-date').textContent = formatDate(order.created_at);
            document.getElementById('modal-service-name').textContent = order.service_name;
            document.getElementById('modal-order-total').textContent = `£${parseFloat(order.total).toFixed(2)}`;
            
            // Render Timeline
            const timelineBox = document.getElementById('modal-order-timeline');
            timelineBox.innerHTML = timeline.map(step => {
                const stepClass = step.status.toLowerCase();
                const icon = step.status === 'Completed' ? '✓' : (step.status === 'Current' ? '•' : '');
                return `
                    <div class="timeline-step ${stepClass}">
                        <div class="timeline-dot">${icon}</div>
                        <div class="timeline-content">
                            <h5>${step.title}</h5>
                            <p>${step.status} • ${step.step_date ? formatDate(step.step_date) : 'Pending'}</p>
                        </div>
                    </div>
                `;
            }).join('');
            
            document.getElementById('order-details-modal').classList.add('active');
        }
    } catch (err) {
        console.error('Error fetching order details:', err);
    }
}

function closeOrderModal() {
    document.getElementById('order-details-modal').classList.remove('active');
}

// ----------------------------------------------------
// CLIENT DASHBOARD (Brixen Consultants Layout)
// ----------------------------------------------------
async function loadClientDashboard() {
    const grid = document.getElementById('dash-active-orders-grid');
    if (grid) {
        // Render skeleton loading state
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

    try {
        const res = await fetch('/api/client/dashboard');
        if (!res.ok) throw new Error(`HTTP error! status: ${res.status}`);
        const data = await res.json();
        
        if (data.status === 'success') {
            // Welcome Hero
            const heroDate = document.getElementById('hero-current-date');
            const heroName = document.getElementById('hero-user-name');
            const heroComps = document.getElementById('hero-companies-count');
            
            if (heroDate) heroDate.textContent = data.current_date;
            if (heroName) heroName.textContent = data.user_name;
            if (heroComps) heroComps.textContent = `${data.total_companies}`;
            
            // 3 Stat Cards
            document.getElementById('dash-card-portfolio-count').textContent = data.total_companies;
            document.getElementById('dash-uk-count').textContent = `${data.uk_companies} UK`;
            document.getElementById('dash-usa-count').textContent = `${data.intl_companies} International`;
            
            document.getElementById('dash-card-overdue-count').textContent = data.overdue_deadlines;
            const overdueBadge = document.getElementById('dash-overdue-badge');
            if (data.overdue_deadlines === 0) {
                overdueBadge.className = 'health-badge success';
                overdueBadge.setAttribute('aria-label', 'All compliance items on track');
                overdueBadge.innerHTML = `<i data-lucide="check-circle-2"></i> All on track`;
            } else {
                overdueBadge.className = 'health-badge warning';
                overdueBadge.setAttribute('aria-label', `${data.overdue_deadlines} overdue compliance items`);
                overdueBadge.innerHTML = `<i data-lucide="alert-triangle"></i> ${data.overdue_deadlines} Overdue`;
            }
            
            document.getElementById('dash-card-upcoming-count').textContent = data.upcoming_deadlines;
            const upcomingBadge = document.getElementById('dash-upcoming-badge');
            if (data.upcoming_deadlines === 0) {
                upcomingBadge.className = 'health-badge info';
                upcomingBadge.setAttribute('aria-label', 'No upcoming compliance deadlines soon');
                upcomingBadge.innerHTML = `<i data-lucide="calendar"></i> No deadlines soon`;
            } else {
                upcomingBadge.className = 'health-badge info';
                upcomingBadge.setAttribute('aria-label', `${data.upcoming_deadlines} upcoming compliance items`);
                upcomingBadge.innerHTML = `<i data-lucide="clock"></i> ${data.upcoming_deadlines} Due Soon`;
            }
            
            // Active Orders Tracker Count
            document.getElementById('dash-active-orders-num').textContent = data.active_orders_count;
            
            // Render Active Orders 3-Column Grid
            renderActiveOrdersGrid(data.active_orders);
            lucide.createIcons();
        } else {
            throw new Error(data.message || 'Failed to load dashboard data.');
        }
    } catch (err) {
        console.error('Error loading dashboard:', err);
        if (grid) {
            grid.innerHTML = `
                <div style="grid-column: 1 / -1; text-align:center; padding:40px; background:#fff; border-radius:16px; border:1px solid #fee2e2;" role="alert">
                    <i data-lucide="alert-circle" style="width:40px; height:40px; color:#ef4444;"></i>
                    <h3 style="font-size:1.05rem; font-weight:700; color:#0f172a; margin-top:10px;">Unable to load dashboard data.</h3>
                    <p style="color:#64748b; font-size:0.85rem; margin-top:4px;">Please check your connection and try again.</p>
                    <button class="btn-primary" style="margin-top:16px;" onclick="loadClientDashboard()">
                        <i data-lucide="refresh-cw"></i> Retry
                    </button>
                </div>
            `;
            lucide.createIcons();
        }
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
        const statusClass = order.status.toLowerCase().replace(' ', '-');
        const itemsCountLabel = `${order.done_items_count} of ${order.total_items_count} done`;
        
        // Build horizontal timeline stages
        const stagesHtml = order.stages.map((stg, i) => {
            const iconSymbol = stg.status === 'completed' ? '✓' : (i + 1).toString();
            return `
                <div class="timeline-stage-item ${stg.status}">
                    <div class="stage-icon-circle">${iconSymbol}</div>
                    <div class="stage-label-text">${stg.name}</div>
                </div>
            `;
        }).join('');
        
        // Build sub-services table
        const servicesRowsHtml = order.service_items.map(s => {
            const badgeClass = s.status === 'Done' ? 'completed' : (s.status === 'In Progress' ? 'processing' : 'pending');
            return `
                <tr>
                    <td>${s.name}</td>
                    <td style="text-align:right;"><span class="status-badge ${badgeClass}">${s.status}</span></td>
                </tr>
            `;
        }).join('');
        
        return `
            <div class="order-card">
                <div class="order-card-header">
                    <div>
                        <div class="order-number">${order.order_number}</div>
                        <div class="order-date">${itemsCountLabel}</div>
                    </div>
                    <span class="status-badge ${statusClass}">${order.status}</span>
                </div>

                <div>
                    <div class="order-service-title">${order.service_name}</div>
                    <div class="order-price">£${parseFloat(order.total).toFixed(2)}</div>
                </div>

                <div class="order-progress-section">
                    <div class="progress-labels">
                        <span>${itemsCountLabel}</span>
                        <span>${order.progress_percent}%</span>
                    </div>
                    <div class="progress-track">
                        <div class="progress-bar ${isCompleted ? 'completed' : ''}" style="width: ${order.progress_percent}%;"></div>
                    </div>
                </div>

                <!-- Horizontal Stage Timeline -->
                <div class="card-horizontal-timeline">
                    ${stagesHtml}
                </div>

                <!-- Order Services Sub-Table -->
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

                <div class="order-card-footer">
                    <span class="delivery-tag">${order.company_name || 'Personal Account'}</span>
                    <button class="btn-details" onclick="openOrderDetailsModal(${order.id})">View Details</button>
                </div>
            </div>
        `;
    }).join('');
}

async function loadClientCompanies() {
    try {
        const res = await fetch('/api/client/companies');
        const data = await res.json();
        const tbody = document.getElementById('companies-table-body');
        if (tbody && data.status === 'success') {
            tbody.innerHTML = data.companies.map(c => `
                <tr>
                    <td style="font-weight:700;">${c.name}</td>
                    <td><code>${c.company_number}</code></td>
                    <td>${c.inc_date}</td>
                    <td>${c.director}</td>
                    <td>${c.package}</td>
                    <td><span class="status-badge completed">${c.account_status}</span></td>
                    <td><button class="btn-secondary" style="padding:4px 10px; font-size:0.75rem;">View</button></td>
                </tr>
            `).join('');
        }
    } catch (err) { console.error(err); }
}

async function loadClientAddresses() {
    try {
        const res = await fetch('/api/client/addresses');
        const data = await res.json();
        const tbody = document.getElementById('addresses-table-body');
        if (tbody && data.status === 'success') {
            tbody.innerHTML = data.addresses.map(a => `
                <tr>
                    <td style="font-weight:700; color:#7c3aed;">${a.type}</td>
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
                    <td style="font-weight:700;">${d.name}</td>
                    <td>${d.category}</td>
                    <td>${d.file_size}</td>
                    <td>${d.uploaded_by}</td>
                    <td><span class="status-badge ${d.status === 'Approved' ? 'completed' : 'pending'}">${d.status}</span></td>
                    <td><a href="/api/documents/${d.id}/download" class="btn-secondary" style="padding:4px 10px; font-size:0.75rem; text-decoration:none;">Download</a></td>
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
    try {
        const res = await fetch('/api/admin/stats');
        const data = await res.json();
        if (data.status === 'success') {
            document.getElementById('adm-tot-cust').textContent = data.stats.total_customers;
            document.getElementById('adm-tot-ord').textContent = data.stats.total_orders;
            document.getElementById('adm-tot-rev').textContent = data.stats.total_revenue;
            document.getElementById('adm-tot-tix').textContent = data.stats.open_tickets;
            
            // Render Revenue Analytics Chart
            renderAdminChart(data.charts.monthly);
        }
    } catch (err) { console.error(err); }
}

function renderAdminChart(monthlyData) {
    const ctx = document.getElementById('admin-chart-canvas');
    if (!ctx) return;
    
    if (adminChart) adminChart.destroy();
    
    const labels = monthlyData.map(m => m.month || 'Month');
    const values = monthlyData.map(m => m.rev || 1000);
    
    adminChart = new Chart(ctx, {
        type: 'line',
        data: {
            labels: labels,
            datasets: [{
                label: 'Revenue (£)',
                data: values,
                borderColor: '#003971',
                backgroundColor: 'rgba(0, 57, 113, 0.1)',
                fill: true,
                tension: 0.4
            }]
        },
        options: {
            responsive: true,
            plugins: { legend: { display: false } }
        }
    });
}

async function loadAdminOrders() {
    try {
        const res = await fetch('/api/admin/orders');
        const data = await res.json();
        const tbody = document.getElementById('adm-orders-table-body');
        if (tbody && data.status === 'success') {
            tbody.innerHTML = data.orders.slice(0, 15).map(o => `
                <tr>
                    <td style="font-weight:700; color:#7c3aed;">${o.order_number}</td>
                    <td>${o.client_name}</td>
                    <td>${o.service_name}</td>
                    <td>£${parseFloat(o.total).toFixed(2)}</td>
                    <td>${o.progress_percent}%</td>
                    <td>
                        <select onchange="updateAdminOrderStatus(${o.id}, this.value)" style="padding:4px 8px; border-radius:6px; border:1px solid #cbd5e1; font-weight:600;">
                            <option value="Processing" ${o.status === 'Processing' ? 'selected' : ''}>Processing</option>
                            <option value="In Progress" ${o.status === 'In Progress' ? 'selected' : ''}>In Progress</option>
                            <option value="Completed" ${o.status === 'Completed' ? 'selected' : ''}>Completed</option>
                            <option value="Cancelled" ${o.status === 'Cancelled' ? 'selected' : ''}>Cancelled</option>
                        </select>
                    </td>
                    <td><button class="btn-primary" style="padding:4px 10px; font-size:0.75rem;" onclick="advanceOrderProgress(${o.id}, ${o.progress_percent})">+15% Progress</button></td>
                </tr>
            `).join('');
        }
    } catch (err) { console.error(err); }
}

async function updateAdminOrderStatus(orderId, newStatus) {
    let progress = newStatus === 'Completed' ? 100 : 50;
    try {
        await fetch(`/api/admin/orders/${orderId}`, {
            method: 'PUT',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ status: newStatus, progress_percent: progress })
        });
        alert(`Order #${orderId} status updated to ${newStatus}. Client notified.`);
        loadAdminOrders();
    } catch (err) { console.error(err); }
}

async function advanceOrderProgress(orderId, currentProgress) {
    let newProgress = Math.min(100, currentProgress + 15);
    let newStatus = newProgress === 100 ? 'Completed' : 'In Progress';
    try {
        await fetch(`/api/admin/orders/${orderId}`, {
            method: 'PUT',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ progress_percent: newProgress, status: newStatus })
        });
        loadAdminOrders();
    } catch (err) { console.error(err); }
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
    const newBtn = document.getElementById('btn-new-task');
    if (newBtn) newBtn.style.display = canAssign ? 'inline-flex' : 'none';
    const assigneeFilter = document.getElementById('filter-task-assignee');
    if (assigneeFilter) assigneeFilter.style.display = canAssign ? '' : 'none';
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
    const res = await fetch('/api/admin/customers');
    const data = await res.json();
    if (!res.ok || data.status !== 'success') {
        throw new Error(data.message || 'Unable to load clients.');
    }
    taskCustomerCache = data.customers || [];
    return taskCustomerCache;
}

function fillStaffSelect(selectId, selectedId, includeAllLabel) {
    const sel = document.getElementById(selectId);
    if (!sel) return;
    const first = includeAllLabel || 'Unassigned';
    sel.innerHTML = `<option value="">${escapeHtml(first)}</option>` + taskStaffCache.map(s =>
        `<option value="${s.id}">${escapeHtml(s.full_name)} (${escapeHtml(s.role)})</option>`
    ).join('');
    if (selectedId) sel.value = String(selectedId);
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
    applyTaskUiPermissions();
    showTasksError('');
    const tbody = document.getElementById('adm-tasks-table-body');
    if (tbody) {
        tbody.innerHTML = `<tr><td colspan="8" style="text-align:center; color:#64748b; padding:28px;">Loading tasks...</td></tr>`;
    }

    try {
        await fetchTaskStaff();
        const assigneeSel = document.getElementById('filter-task-assignee');
        const previousAssignee = assigneeSel ? assigneeSel.value : '';
        fillStaffSelect('filter-task-assignee', previousAssignee, 'All Assignees');
        if (assigneeSel && previousAssignee) assigneeSel.value = previousAssignee;

        const params = new URLSearchParams();
        const status = document.getElementById('filter-task-status')?.value;
        const priority = document.getElementById('filter-task-priority')?.value;
        const assignee = document.getElementById('filter-task-assignee')?.value;
        const due = document.getElementById('filter-task-due')?.value;
        if (status) params.set('status', status);
        if (priority) params.set('priority', priority);
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
            tbody.innerHTML = `<tr><td colspan="8" style="text-align:center; color:#dc2626; padding:28px;">Unable to load tasks. Please try again.</td></tr>`;
        }
    }
}

function renderAdminTasks(tasks) {
    const tbody = document.getElementById('adm-tasks-table-body');
    if (!tbody) return;
    if (!tasks.length) {
        tbody.innerHTML = `<tr><td colspan="8" style="text-align:center; color:#64748b; padding:36px;">No tasks found.</td></tr>`;
        lucide.createIcons();
        return;
    }
    tbody.innerHTML = tasks.map(t => {
        const canComplete = t.status !== 'Completed' && t.status !== 'Cancelled';
        return `
            <tr>
                <td style="font-weight:700;">${escapeHtml(t.title)}</td>
                <td>${escapeHtml(t.assigned_staff_name || 'Unassigned')}</td>
                <td>${escapeHtml(t.client_name || '—')}</td>
                <td style="font-weight:600; color:#7c3aed;">${escapeHtml(t.order_number || '—')}</td>
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

async function openNewTaskModal() {
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
        fillStaffSelect('new-task-assignee', '', 'Unassigned');
        fillClientSelect('new-task-client', '');
        fillCompanyOrderSelects('new', { companies: [], orders: [] }, '', '');
        document.getElementById('modal-new-task').classList.add('active');
        lucide.createIcons();
    } catch (err) {
        alert(err.message || 'Unable to open new task form.');
    }
}

function closeNewTaskModal() {
    const modal = document.getElementById('modal-new-task');
    if (modal) modal.classList.remove('active');
}

function collectTaskForm(prefix, includeStatus) {
    const payload = {
        title: document.getElementById(`${prefix}-task-title`).value.trim(),
        description: document.getElementById(`${prefix}-task-description`).value,
        priority: document.getElementById(`${prefix}-task-priority`).value,
        due_date: document.getElementById(`${prefix}-task-due`).value || null,
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
        fillStaffSelect('edit-task-assignee', t.assigned_staff_id, 'Unassigned');
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

async function loadAdminCustomers() {
    try {
        const res = await fetch('/api/admin/customers');
        const data = await res.json();
        const tbody = document.getElementById('adm-customers-table-body');
        if (tbody && data.status === 'success') {
            tbody.innerHTML = data.customers.map(c => `
                <tr>
                    <td style="font-weight:700;">${c.full_name}</td>
                    <td>${c.email}</td>
                    <td>${c.companies_count}</td>
                    <td>${c.orders_count}</td>
                    <td><span class="status-badge completed">${c.status}</span></td>
                    <td>
                        <button class="btn-primary" style="padding:4px 10px; font-size:0.75rem;" onclick="openCrmClientModal(${c.id})">View CRM Profile</button>
                    </td>
                </tr>
            `).join('');
        }
    } catch (err) { console.error(err); }
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
                <div style="padding:10px; border:1px solid #e2e8f0; border-radius:8px; margin-bottom:8px;">
                    <strong>${o.order_number}</strong> — ${o.service_name} (£${parseFloat(o.total).toFixed(2)})
                    <div style="font-size:0.78rem; color:#64748b;">Progress: ${o.progress_percent}% | Status: ${o.status}</div>
                </div>
            `).join('') : '<p style="color:#64748b;">No order history available.</p>';
            
            // Populate Invoices tab
            const invBox = document.getElementById('crm-invoices-list');
            invBox.innerHTML = data.invoices.length ? data.invoices.map(inv => `
                <div style="padding:10px; border:1px solid #e2e8f0; border-radius:8px; margin-bottom:8px;">
                    <strong>${inv.invoice_number}</strong> — £${parseFloat(inv.total).toFixed(2)} (${inv.status})
                </div>
            `).join('') : '<p style="color:#64748b;">No invoices available.</p>';
            
            // Populate Documents tab
            const docBox = document.getElementById('crm-documents-list');
            docBox.innerHTML = data.documents.length ? data.documents.map(doc => `
                <div style="padding:10px; border:1px solid #e2e8f0; border-radius:8px; margin-bottom:8px; display:flex; justify-content:space-between; align-items:center;">
                    <div>
                        <strong>${doc.name}</strong> (${doc.category})
                        <div style="font-size:0.78rem; color:#64748b;">Uploaded: ${formatDate(doc.created_at)}</div>
                    </div>
                    <a href="/api/documents/${doc.id}/download" target="_blank" class="btn-primary" style="padding:4px 10px; font-size:0.75rem;">Download</a>
                </div>
            `).join('') : '<p style="color:#64748b;">No documents uploaded.</p>';
            
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

async function loadAdminDocuments() {
    try {
        const res = await fetch('/api/admin/documents');
        const data = await res.json();
        const tbody = document.getElementById('adm-documents-table-body');
        if (tbody && data.status === 'success') {
            tbody.innerHTML = data.documents.map(d => `
                <tr>
                    <td>${d.client_name}</td>
                    <td style="font-weight:700;">${d.name}</td>
                    <td>${d.category}</td>
                    <td><span class="status-badge ${d.status === 'Approved' ? 'completed' : 'pending'}">${d.status}</span></td>
                    <td>
                        <button class="btn-primary" style="padding:4px 10px; font-size:0.75rem;" onclick="approveDocument(${d.id})">Approve</button>
                    </td>
                </tr>
            `).join('');
        }
    } catch (err) { console.error(err); }
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
                    <td style="font-weight:700; color:#7c3aed;">${l.user_email}</td>
                    <td><code>${l.action}</code></td>
                    <td>${l.details}</td>
                </tr>
            `).join('');
        }
    } catch (err) { console.error(err); }
}

async function loadAdminSettings() {
    try {
        const res = await fetch('/api/settings');
        const data = await res.json();
        if (data.status === 'success') {
            const s = data.settings;
            if (s.company_name) document.getElementById('set-company-name').value = s.company_name;
            if (s.support_email) document.getElementById('set-support-email').value = s.support_email;
            if (s.currency) document.getElementById('set-currency').value = s.currency;
            if (s.order_prefix) document.getElementById('set-order-prefix').value = s.order_prefix;
        }
    } catch (err) { console.error(err); }
}

async function saveAdminSettings(e) {
    e.preventDefault();
    const company_name = document.getElementById('set-company-name').value;
    const support_email = document.getElementById('set-support-email').value;
    const currency = document.getElementById('set-currency').value;
    const order_prefix = document.getElementById('set-order-prefix').value;
    
    try {
        await fetch('/api/admin/settings', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ company_name, support_email, currency, order_prefix })
        });
        alert('Admin settings saved! Branding updated.');
        document.querySelectorAll('.brand-name-display').forEach(el => el.textContent = company_name);
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

function formatDateTime(dateStr) {
    if (!dateStr) return '';
    const d = new Date(dateStr);
    return d.toLocaleString('en-GB', { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' });
}

function handleGlobalSearch(query) {
    if (activeView === 'client-orders') {
        loadClientOrders();
    }
}

function formatDate(dateStr) {
    if (!dateStr) return '';
    const d = new Date(dateStr);
    return d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' });
}
