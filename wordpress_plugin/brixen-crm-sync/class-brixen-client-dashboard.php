<?php
/**
 * Client home for WooCommerce My Account.
 * Designed like a personal CRM: identity, next step, recent work, then details.
 *
 * @package BrixenCRMSync
 */

if (!defined('ABSPATH')) {
    exit;
}

class Brixen_CRM_Client_Dashboard {

    public static function init() {
        if (class_exists('Brixen_CRM_Portal_SSO') && Brixen_CRM_Portal_SSO::is_configured()) {
            return;
        }
        add_action('wp_enqueue_scripts', array(__CLASS__, 'enqueue_assets'));
        add_action('wp_loaded', array(__CLASS__, 'remove_default_dashboard'));
        add_action('woocommerce_account_dashboard', array(__CLASS__, 'render_dashboard'), 1);
        add_filter('woocommerce_get_dashboard_text', array(__CLASS__, 'filter_dashboard_text'), 10, 2);
        add_filter('woocommerce_account_menu_items', array(__CLASS__, 'simplify_menu_items'), 999);
    }

    public static function remove_default_dashboard() {
        remove_action('woocommerce_account_dashboard', 'woocommerce_account_dashboard', 10);
    }

    public static function enqueue_assets() {
        if (!function_exists('is_account_page') || !is_account_page()) {
            return;
        }
        wp_register_style('brixen-client-dashboard', false, array(), '1.4.0');
        wp_enqueue_style('brixen-client-dashboard');
        wp_add_inline_style('brixen-client-dashboard', self::css());
        wp_register_script('brixen-client-dashboard', false, array(), '1.4.0', true);
        wp_enqueue_script('brixen-client-dashboard');
        wp_localize_script('brixen-client-dashboard', 'brixenClient', array(
            'loggedIn' => is_user_logged_in(),
            'hi'       => self::greeting_text(),
        ));
        wp_add_inline_script('brixen-client-dashboard', self::js());
    }

    public static function greeting_text() {
        if (!is_user_logged_in()) {
            return __('Welcome', 'brixen-crm-sync');
        }
        $name = self::welcome_name(self::get_profile());
        if ($name === '') {
            return __('Welcome', 'brixen-crm-sync');
        }
        return sprintf(__('Welcome, %s', 'brixen-crm-sync'), $name);
    }

    private static function welcome_name($profile) {
        $full = self::usable_name($profile['full_name'] ?? '');
        if ($full !== '') {
            return $full;
        }
        return self::usable_name($profile['first_name'] ?? '');
    }

    private static function usable_name($name) {
        $name = trim(preg_replace('/\s+/', ' ', (string) $name));
        if ($name === '' || self::looks_like_placeholder_name($name)) {
            return '';
        }
        return $name;
    }

    public static function filter_dashboard_text($text, $user) {
        return '';
    }

    public static function simplify_menu_items($items) {
        if (!is_array($items)) {
            return $items;
        }
        $keep = array(
            'dashboard'         => __('Home', 'brixen-crm-sync'),
            'orders'            => __('Orders', 'brixen-crm-sync'),
            'customer-messages' => __('Files', 'brixen-crm-sync'),
            'edit-account'      => __('Account', 'brixen-crm-sync'),
            'customer-logout'   => __('Log out', 'brixen-crm-sync'),
        );
        $simple = array();
        foreach ($keep as $key => $label) {
            if (isset($items[$key])) {
                $simple[$key] = $label;
            }
        }
        return $simple ? $simple : $items;
    }

    public static function get_profile($user_id = 0) {
        $user_id = $user_id ?: get_current_user_id();
        $user = $user_id ? get_userdata($user_id) : null;
        if (!$user) {
            return array();
        }

        $cfs = get_user_meta($user_id, '_cfs_form_draft', true);
        if (!is_array($cfs)) {
            $cfs = array();
        }

        if (empty($cfs['director_name']) && function_exists('wc_get_orders')) {
            $orders = wc_get_orders(array(
                'customer_id' => $user_id,
                'limit'       => 8,
                'orderby'     => 'date',
                'order'       => 'DESC',
                'return'      => 'objects',
            ));
            foreach ($orders as $order) {
                $blob = $order->get_meta('_cfs_form_data');
                if (is_array($blob) && !empty($blob['director_name'])) {
                    $cfs = array_merge($cfs, $blob);
                    break;
                }
                $director = $order->get_meta('_cfs_director_name');
                if ($director) {
                    $cfs['director_name'] = $director;
                    foreach (array(
                        'registered_email' => '_cfs_registered_email',
                        'uk_contact_number' => '_cfs_uk_contact_number',
                        'date_of_birth' => '_cfs_date_of_birth',
                        'address_street' => '_cfs_address_street',
                        'address_line2' => '_cfs_address_line2',
                        'address_city' => '_cfs_address_city',
                        'address_state' => '_cfs_address_state',
                        'address_zip' => '_cfs_address_zip',
                        'address_country' => '_cfs_address_country',
                        'company_name' => '_cfs_company_name',
                        'registered_address_street' => '_cfs_registered_address_street',
                        'registered_address_line2' => '_cfs_registered_address_line2',
                        'registered_address_city' => '_cfs_registered_address_city',
                        'registered_address_state' => '_cfs_registered_address_state',
                        'registered_address_zip' => '_cfs_registered_address_zip',
                    ) as $cfs_key => $meta_key) {
                        if (empty($cfs[$cfs_key])) {
                            $val = $order->get_meta($meta_key);
                            if ($val) {
                                $cfs[$cfs_key] = $val;
                            }
                        }
                    }
                    break;
                }
            }
        }

        $first = trim((string) get_user_meta($user_id, 'first_name', true));
        $last = trim((string) get_user_meta($user_id, 'last_name', true));
        $billing_first = trim((string) get_user_meta($user_id, 'billing_first_name', true));
        $billing_last = trim((string) get_user_meta($user_id, 'billing_last_name', true));
        $wc_name = trim($first . ' ' . $last);
        $billing_full = trim($billing_first . ' ' . $billing_last);
        $director = trim((string) ($cfs['director_name'] ?? ''));
        $display = trim((string) $user->display_name);

        $order_first = '';
        $order_full = '';
        if (function_exists('wc_get_orders')) {
            $name_orders = wc_get_orders(array(
                'customer_id' => $user_id,
                'limit'       => 5,
                'orderby'     => 'date',
                'order'       => 'DESC',
                'return'      => 'objects',
            ));
            foreach ($name_orders as $order) {
                $of = trim((string) $order->get_billing_first_name());
                $ol = trim((string) $order->get_billing_last_name());
                if ($order_first === '' && self::usable_name($of) !== '') {
                    $order_first = $of;
                    $order_full = trim($of . ' ' . $ol);
                }
                $dir = trim((string) $order->get_meta('_cfs_director_name'));
                if ($director === '' && self::usable_name($dir) !== '') {
                    $director = $dir;
                }
            }
        }

        $full_name = self::usable_name($director)
            ?: self::usable_name($order_full)
            ?: self::usable_name($billing_full)
            ?: self::usable_name($wc_name)
            ?: self::usable_name($display);

        $first_name = self::usable_name($billing_first)
            ?: self::usable_name($order_first)
            ?: self::usable_name($first);
        if ($first_name === '' && $full_name !== '') {
            $parts = preg_split('/\s+/', $full_name);
            $first_name = self::usable_name($parts[0] ?? '');
        }

        $email = trim((string) ($cfs['registered_email'] ?? $user->user_email));
        $phone = trim((string) ($cfs['uk_contact_number'] ?? ''));
        if ($phone === '') {
            $phone = trim((string) get_user_meta($user_id, 'billing_phone', true));
        }
        $dob = trim((string) ($cfs['date_of_birth'] ?? ''));
        if ($dob === '') {
            foreach (array('date_of_birth', 'dob', 'birth_date') as $key) {
                $val = trim((string) get_user_meta($user_id, $key, true));
                if ($val !== '') {
                    $dob = $val;
                    break;
                }
            }
        }

        $home_parts = array(
            'line1'    => $cfs['address_street'] ?? get_user_meta($user_id, 'billing_address_1', true),
            'line2'    => $cfs['address_line2'] ?? get_user_meta($user_id, 'billing_address_2', true),
            'city'     => $cfs['address_city'] ?? get_user_meta($user_id, 'billing_city', true),
            'state'    => $cfs['address_state'] ?? get_user_meta($user_id, 'billing_state', true),
            'postcode' => $cfs['address_zip'] ?? get_user_meta($user_id, 'billing_postcode', true),
            'country'  => $cfs['address_country'] ?? self::country_label(get_user_meta($user_id, 'billing_country', true)),
        );
        $office_parts = array(
            'line1'    => $cfs['registered_address_street'] ?? '',
            'line2'    => $cfs['registered_address_line2'] ?? '',
            'city'     => $cfs['registered_address_city'] ?? '',
            'state'    => $cfs['registered_address_state'] ?? '',
            'postcode' => $cfs['registered_address_zip'] ?? '',
            'country'  => $cfs['registered_address_country'] ?? 'United Kingdom',
        );

        $company_name = trim((string) ($cfs['company_name'] ?? ''));
        if ($company_name === '') {
            $company_name = trim((string) get_user_meta($user_id, 'billing_company', true));
        }

        return array(
            'full_name'          => $full_name,
            'first_name'         => $first_name,
            'email'              => $email,
            'phone'              => $phone,
            'date_of_birth'      => $dob,
            'home_parts'         => $home_parts,
            'personal_address'   => self::format_address($home_parts),
            'company_name'       => $company_name,
            'office_parts'       => $office_parts,
            'registered_address' => self::format_address($office_parts),
            'has_company'        => ($company_name !== '' || self::format_address($office_parts) !== ''),
        );
    }

    public static function get_recent_orders($user_id = 0, $limit = 4) {
        $user_id = $user_id ?: get_current_user_id();
        if (!$user_id || !function_exists('wc_get_orders')) {
            return array();
        }
        $orders = wc_get_orders(array(
            'customer_id' => $user_id,
            'limit'       => $limit,
            'orderby'     => 'date',
            'order'       => 'DESC',
            'return'      => 'objects',
        ));
        $rows = array();
        foreach ($orders as $order) {
            $names = array();
            foreach ($order->get_items() as $item) {
                $names[] = $item->get_name();
            }
            $status = $order->get_status();
            $rows[] = array(
                'id'      => $order->get_id(),
                'number'  => $order->get_order_number(),
                'title'   => $names ? implode(', ', $names) : __('Order', 'brixen-crm-sync'),
                'status'  => wc_get_order_status_name($status),
                'tone'    => self::status_tone($status),
                'date'    => $order->get_date_created() ? $order->get_date_created()->date_i18n('j M Y') : '',
                'url'     => $order->get_view_order_url(),
            );
        }
        return $rows;
    }

    private static function status_tone($status) {
        $status = strtolower((string) $status);
        if (in_array($status, array('completed', 'processing'), true)) {
            return 'good';
        }
        if (in_array($status, array('pending', 'on-hold', 'failed'), true)) {
            return 'warn';
        }
        if (in_array($status, array('cancelled', 'refunded'), true)) {
            return 'muted';
        }
        return 'info';
    }

    private static function looks_like_placeholder_name($name) {
        $lower = strtolower(trim((string) $name));
        if ($lower === '') {
            return true;
        }
        $bad = array('brixen', 'brixen consultant', 'brixen consultants', 'admin', 'customer', 'user');
        foreach ($bad as $token) {
            if ($lower === $token || strpos($lower, $token) === 0) {
                return true;
            }
        }
        return false;
    }

    private static function country_label($code) {
        $code = strtoupper(trim((string) $code));
        if ($code === '') {
            return '';
        }
        if (function_exists('WC') && WC()->countries) {
            $countries = WC()->countries->get_countries();
            if (isset($countries[$code])) {
                return $countries[$code];
            }
        }
        return $code;
    }

    private static function format_address($parts) {
        $chunks = array();
        foreach (array('line1', 'line2', 'city', 'state', 'postcode', 'country') as $key) {
            $val = trim((string) ($parts[$key] ?? ''));
            if ($val !== '') {
                $chunks[] = $val;
            }
        }
        return implode(', ', $chunks);
    }

    private static function render_address_block($parts) {
        $lines = array();
        foreach (array('line1', 'line2', 'city', 'state', 'postcode', 'country') as $key) {
            $val = trim((string) ($parts[$key] ?? ''));
            if ($val !== '') {
                $lines[] = $val;
            }
        }
        if (!$lines) {
            return '';
        }
        $html = '<address class="bcd-address">';
        foreach ($lines as $line) {
            $html .= '<span>' . esc_html($line) . '</span>';
        }
        $html .= '</address>';
        return $html;
    }

    private static function format_dob($raw) {
        $raw = trim((string) $raw);
        if ($raw === '') {
            return '';
        }
        $ts = strtotime($raw);
        if ($ts) {
            return date_i18n(get_option('date_format'), $ts);
        }
        return $raw;
    }

    private static function initials($name) {
        $parts = preg_split('/\s+/', trim((string) $name));
        $letters = '';
        foreach ($parts as $part) {
            if ($part !== '') {
                $letters .= strtoupper(substr($part, 0, 1));
            }
            if (strlen($letters) >= 2) {
                break;
            }
        }
        return $letters !== '' ? $letters : 'BC';
    }

    private static function next_step($profile, $orders) {
        if (!$orders) {
            return array(
                'title' => __('Start your first order', 'brixen-crm-sync'),
                'copy'  => __('Choose a package when you are ready. We will keep everything in this account.', 'brixen-crm-sync'),
                'label' => __('Browse packages', 'brixen-crm-sync'),
                'url'   => home_url('/'),
            );
        }
        foreach ($orders as $order) {
            if ($order['tone'] === 'warn') {
                return array(
                    'title' => __('Action needed on an order', 'brixen-crm-sync'),
                    'copy'  => sprintf(__('Order #%s is %s. Open it to finish payment or check the next step.', 'brixen-crm-sync'), $order['number'], strtolower($order['status'])),
                    'label' => __('Open order', 'brixen-crm-sync'),
                    'url'   => $order['url'],
                );
            }
        }
        if (empty($profile['personal_address'])) {
            return array(
                'title' => __('Add your home address', 'brixen-crm-sync'),
                'copy'  => __('Your personal address is used for director records. It stays separate from the company office.', 'brixen-crm-sync'),
                'label' => __('Update account', 'brixen-crm-sync'),
                'url'   => wc_get_endpoint_url('edit-account', '', wc_get_page_permalink('myaccount')),
            );
        }
        return array(
            'title' => __('You are all set', 'brixen-crm-sync'),
            'copy'  => __('Use Messages & files if our team shares certificates or asks for a document.', 'brixen-crm-sync'),
            'label' => __('Open files', 'brixen-crm-sync'),
            'url'   => wc_get_endpoint_url('customer-messages', '', wc_get_page_permalink('myaccount')),
        );
    }

    public static function render_dashboard() {
        if (!is_user_logged_in()) {
            return;
        }

        $profile = self::get_profile();
        $orders = self::get_recent_orders(0, 8);
        $hello = self::greeting_text();
        $company = $profile['company_name'] ?: '';

        ?>
        <div class="brixen-client-dashboard">
            <h1 class="bcd-title"><?php echo esc_html($hello); ?></h1>
            <?php if ($company) : ?>
            <p class="bcd-lead"><?php echo esc_html($company); ?></p>
            <?php endif; ?>

            <section class="bcd-card">
                <table class="bcd-table">
                    <thead>
                        <tr>
                            <th><?php esc_html_e('Order', 'brixen-crm-sync'); ?></th>
                            <th><?php esc_html_e('Started on', 'brixen-crm-sync'); ?></th>
                            <th><?php esc_html_e('Status', 'brixen-crm-sync'); ?></th>
                        </tr>
                    </thead>
                    <tbody>
                        <?php if ($orders) : ?>
                            <?php foreach ($orders as $order) : ?>
                            <tr>
                                <td>
                                    <a href="<?php echo esc_url($order['url']); ?>">
                                        <strong>#<?php echo esc_html($order['number']); ?></strong>
                                        <span><?php echo esc_html($order['title']); ?></span>
                                    </a>
                                </td>
                                <td><?php echo esc_html($order['date'] ?: '—'); ?></td>
                                <td><span class="bcd-pill bcd-pill-<?php echo esc_attr($order['tone']); ?>"><?php echo esc_html($order['status']); ?></span></td>
                            </tr>
                            <?php endforeach; ?>
                        <?php else : ?>
                            <tr><td colspan="3"><?php esc_html_e('No orders yet.', 'brixen-crm-sync'); ?></td></tr>
                        <?php endif; ?>
                    </tbody>
                </table>
            </section>

            <section class="bcd-card">
                <h2 class="bcd-card-title"><?php esc_html_e('Company details', 'brixen-crm-sync'); ?></h2>
                <dl class="bcd-facts">
                    <div><dt><?php esc_html_e('Name', 'brixen-crm-sync'); ?></dt><dd><?php echo esc_html($profile['full_name'] ?: '—'); ?></dd></div>
                    <div><dt><?php esc_html_e('Email', 'brixen-crm-sync'); ?></dt><dd><?php echo esc_html($profile['email'] ?: '—'); ?></dd></div>
                    <div><dt><?php esc_html_e('Company', 'brixen-crm-sync'); ?></dt><dd><?php echo esc_html($profile['company_name'] ?: __('Not on file yet', 'brixen-crm-sync')); ?></dd></div>
                    <div><dt><?php esc_html_e('Registered office', 'brixen-crm-sync'); ?></dt><dd><?php echo esc_html($profile['registered_address'] ?: __('Will appear after formation', 'brixen-crm-sync')); ?></dd></div>
                </dl>
            </section>
        </div>
        <?php
    }

    private static function css() {
        return <<<'CSS'
body.woocommerce-account .deensimc-marquee-main-container,
body.woocommerce-account [class*="deensimc-marquee"],
body.woocommerce-account .elementor-element-a5b0faf,
body.woocommerce-account .elementor-element-b5cadef,
body.woocommerce-account .bcd-hide-strip,
body.woocommerce-account .bcd-hide-hero,
body.woocommerce-account .bcd-slim-hero,
body.woocommerce-account .elementor-element-45b9ec6 {
  display: none !important;
  height: 0 !important;
  min-height: 0 !important;
  padding: 0 !important;
  margin: 0 !important;
  overflow: hidden !important;
}
.eael-account-dashboard-navbar .eael-account-profile,
.eael-account-dashboard-navbar .eael-account-user,
.woocommerce-account .woocommerce-MyAccount-navigation .hello,
.woocommerce-MyAccount-navigation-link--edit-address,
.woocommerce-MyAccount-navigation-link--downloads,
.woocommerce-MyAccount-navigation-link--payment-methods,
.woocommerce-MyAccount-navigation-link--identity-verification,
.woocommerce-account .woocommerce-MyAccount-navigation li:has(a[href*="company-detail"]),
.woocommerce-account .woocommerce-MyAccount-navigation li:has(a[href*="company-name"]),
.woocommerce-account .woocommerce-MyAccount-navigation li:has(a[href*="my-service"]),
.woocommerce-account .woocommerce-MyAccount-navigation li:has(a[href*="tide"]),
.woocommerce-account .woocommerce-MyAccount-navigation li:has(a[href*="registered-office"]),
.woocommerce-account .woocommerce-MyAccount-navigation li:has(a[href*="dormant"]),
.woocommerce-account .woocommerce-MyAccount-navigation li:has(a[href*="directors-service"]),
.woocommerce-account .woocommerce-MyAccount-navigation li:has(a[href*="company-secretary"]),
.woocommerce-account .woocommerce-MyAccount-navigation li:has(a[href*="identity-verification"]) {
  display: none !important;
}
.logged-in.woocommerce-account .woocommerce-MyAccount-content > p,
.logged-in.woocommerce-account .woocommerce-MyAccount-content > .woocommerce-info,
.woocommerce-MyAccount-content .profile-details,
.woocommerce-MyAccount-content .profile-details-container {
  display: none !important;
}
.brixen-client-dashboard {
  --navy: #003971;
  --ink: #1d1d1f;
  --muted: #6e6e73;
  --line: #eeeae4;
  font-family: inherit;
  color: var(--ink);
  max-width: 100%;
}
.brixen-client-dashboard .bcd-title {
  margin: 0 0 6px;
  font-size: 26px;
  font-weight: 700;
  letter-spacing: -0.02em;
  color: var(--navy);
}
.brixen-client-dashboard .bcd-lead {
  margin: 0 0 18px;
  color: var(--muted);
  font-size: 15px;
}
.bcd-card {
  background: #fff;
  border: 1px solid var(--line);
  border-radius: 12px;
  margin-bottom: 18px;
  overflow: hidden;
}
.bcd-card-title {
  margin: 0;
  padding: 16px 20px 8px;
  font-size: 16px;
  color: var(--navy);
}
.bcd-table { width: 100%; border-collapse: collapse; }
.bcd-table th,
.bcd-table td {
  text-align: left;
  padding: 13px 20px;
  font-size: 14px;
  border-bottom: 1px solid var(--line);
  vertical-align: middle;
}
.bcd-table th {
  font-size: 12px;
  font-weight: 700;
  color: var(--muted);
}
.bcd-table tr:last-child td { border-bottom: 0; }
.bcd-table a { color: inherit !important; text-decoration: none !important; display: grid; gap: 2px; }
.bcd-table a strong { color: var(--navy); }
.bcd-table a span { color: var(--muted); font-size: 13px; }
.bcd-pill {
  display: inline-block;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: .04em;
  text-transform: uppercase;
  border-radius: 980px;
  padding: 5px 10px;
  background: #e8eef5;
  color: var(--navy);
}
.bcd-pill-good { background: #16a34a; color: #fff; }
.bcd-pill-warn { background: #f59e0b; color: #fff; }
.bcd-pill-muted { background: #9ca3af; color: #fff; }
.bcd-facts { margin: 0; padding: 4px 20px 16px; }
.bcd-facts > div { display: grid; grid-template-columns: 150px 1fr; gap: 12px; padding: 10px 0; border-top: 1px solid var(--line); }
.bcd-facts > div:first-child { border-top: 0; }
.bcd-facts dt { margin: 0; color: var(--muted); font-weight: 500; }
.bcd-facts dd { margin: 0; font-weight: 600; }
@media (max-width: 767px) {
  .bcd-facts > div { grid-template-columns: 1fr; gap: 2px; }
}
CSS;
    }

    private static function js() {
        return <<<'JS'
(function(){
  var cfg = window.brixenClient || {};
  var keep = {home:1, orders:1, files:1, account:1, logout:1, 'log out':1};
  var hideNav = /company details|company names|my services|tide bank|registered office|dormant company|directors service|company secretary|identity verification|downloads|payment methods|addresses/;
  function txt(el){ return (el.textContent || '').replace(/\s+/g, ' ').trim(); }

  document.querySelectorAll('.woocommerce-MyAccount-navigation a, .woocommerce-account nav a').forEach(function(a){
    var label = txt(a).toLowerCase();
    if (!label || keep[label]) return;
    if (hideNav.test(label)) {
      var li = a.closest('li') || a;
      li.style.display = 'none';
    }
  });

  document.querySelectorAll('.woocommerce-MyAccount-content > p, .woocommerce-MyAccount-content > .woocommerce-info').forEach(function(el){
    var t = txt(el).toLowerCase();
    if (/^hello |from your account dashboard|not .+ log out/.test(t)) el.style.display = 'none';
  });

  document.querySelectorAll('h1, h2, h3, h4, .item--title, .ct-text-inner, .elementor-heading-title, .ct-heading, .page-title, .entry-title').forEach(function(el){
    var t = txt(el).toLowerCase();
    var isPortal = t === 'client portal';
    var isHi = /^hi[, ]/.test(t) || /^welcome back/.test(t);
    if (!isPortal && !isHi) return;
    if (el.closest('.brixen-client-dashboard')) return;
    var wrap = el.closest('.elementor-top-section, .elementor-element-45b9ec6, .elementor-section, section, .page-header, .entry-header, .ct-title-section, .hero, .banner, .page-banner, .inner-banner') || el.parentElement;
    if (wrap && !wrap.closest('.brixen-client-dashboard') && !wrap.closest('.woocommerce-MyAccount-content')) {
      wrap.classList.add('bcd-hide-hero');
    }
  });

  document.querySelectorAll('.deensimc-marquee-main-container, [class*="deensimc-marquee"]').forEach(function(el){
    var wrap = el.closest('.elementor-top-section, .elementor-section, section') || el;
    wrap.classList.add('bcd-hide-strip');
  });
})();
JS;
    }
}
