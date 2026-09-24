<?php
/**
 * Signed SSO into the Brixen Flask client portal (portal.brixenconsultants.com).
 *
 * @package BrixenCRMSync
 */

if (!defined('ABSPATH')) {
    exit;
}

class Brixen_CRM_Portal_SSO {

    public static function init() {
        add_filter('login_redirect', array(__CLASS__, 'filter_login_redirect'), 20, 3);
        add_filter('woocommerce_login_redirect', array(__CLASS__, 'filter_wc_login_redirect'), 20, 2);
        add_filter('allowed_redirect_hosts', array(__CLASS__, 'allow_portal_redirect_host'), 10, 2);
        add_action('template_redirect', array(__CLASS__, 'maybe_redirect_my_account'), 5);
        // Frontend guest “Log in” → website client panel only (never CRM host, never wp-login.php).
        add_filter('login_url', array(__CLASS__, 'filter_frontend_login_url'), 50, 3);
        add_filter('login_url', array(__CLASS__, 'block_crm_host_login_url'), 1000, 3);
    }

    /**
     * Website client panel login URL (brixenconsultants.com/client-panel/).
     * Hard-rejects portal.brixenconsultants.com and wp-login.php.
     */
    public static function website_client_panel_url() {
        $fallback = home_url('/client-panel/');
        $url = $fallback;
        if (function_exists('wc_get_page_permalink')) {
            $wc = wc_get_page_permalink('myaccount');
            if ($wc) {
                $url = $wc;
            }
        }
        return self::reject_crm_login_target($url, $fallback);
    }

    /**
     * Absolute ban: guest login links must never point at the CRM portal host.
     */
    public static function reject_crm_login_target($url, $fallback = '') {
        $fallback = $fallback !== '' ? $fallback : home_url('/client-panel/');
        $url = is_string($url) ? trim($url) : '';
        if ($url === '') {
            return $fallback;
        }
        $host = strtolower((string) wp_parse_url($url, PHP_URL_HOST));
        $path = strtolower((string) (wp_parse_url($url, PHP_URL_PATH) ?: ''));
        if ($host !== '' && strpos($host, 'portal.brixenconsultants.com') !== false) {
            return $fallback;
        }
        if (strpos($path, 'wp-login.php') !== false) {
            return $fallback;
        }
        return $url;
    }

    /**
     * @deprecated Use website_client_panel_url() for guest login links.
     */
    public static function portal_login_url() {
        return self::website_client_panel_url();
    }

    /**
     * Send guest login links to the website client panel, not WordPress wp-login.php
     * and not portal.brixenconsultants.com.
     */
    public static function filter_frontend_login_url($login_url, $redirect = '', $force_reauth = false) {
        $redirect_to = is_string($redirect) ? $redirect : '';
        if ($redirect_to !== '' && (strpos($redirect_to, 'wp-admin') !== false || preg_match('#/(wp-)?admin(/|$)#', $redirect_to))) {
            return $login_url;
        }
        if (is_admin() || (defined('WP_ADMIN') && WP_ADMIN)) {
            return $login_url;
        }
        $uri = isset($_SERVER['REQUEST_URI']) ? (string) $_SERVER['REQUEST_URI'] : '';
        if (strpos($uri, 'wp-login.php') !== false || strpos($uri, '/wp-admin') !== false) {
            return $login_url;
        }
        return self::website_client_panel_url();
    }

    /**
     * Final guard: whatever earlier filters returned, never allow CRM host as login_url on the storefront.
     */
    public static function block_crm_host_login_url($login_url, $redirect = '', $force_reauth = false) {
        if (is_admin() || (defined('WP_ADMIN') && WP_ADMIN)) {
            return $login_url;
        }
        $uri = isset($_SERVER['REQUEST_URI']) ? (string) $_SERVER['REQUEST_URI'] : '';
        if (strpos($uri, 'wp-login.php') !== false || strpos($uri, '/wp-admin') !== false) {
            return $login_url;
        }
        $redirect_to = is_string($redirect) ? $redirect : '';
        if ($redirect_to !== '' && (strpos($redirect_to, 'wp-admin') !== false || preg_match('#/(wp-)?admin(/|$)#', $redirect_to))) {
            return $login_url;
        }
        return self::reject_crm_login_target($login_url, self::website_client_panel_url());
    }

    public static function allow_portal_redirect_host($hosts, $host = '') {
        $crm_url = Brixen_CRM_Webhook_Sender::get_crm_url();
        if ($crm_url) {
            $parsed = parse_url($crm_url, PHP_URL_HOST);
            if ($parsed && !in_array($parsed, $hosts, true)) {
                $hosts[] = $parsed;
            }
        }
        if (!in_array('portal.brixenconsultants.com', $hosts, true)) {
            $hosts[] = 'portal.brixenconsultants.com';
        }
        return array_unique($hosts);
    }

    public static function is_configured() {
        $url = trim((string) Brixen_CRM_Webhook_Sender::get_crm_url());
        $secret = trim((string) Brixen_CRM_Webhook_Sender::get_webhook_secret());
        return $url !== '' && $secret !== '';
    }

    /**
     * Build a signed SSO URL for the given WP user (defaults to current user).
     *
     * @param int|WP_User|null $user
     * @return string Empty when SSO cannot be built.
     */
    public static function build_url($user = null) {
        if (!self::is_configured()) {
            return '';
        }

        if ($user instanceof WP_User) {
            $wp_user = $user;
        } elseif (is_numeric($user) && (int) $user > 0) {
            $wp_user = get_userdata((int) $user);
        } elseif (is_user_logged_in()) {
            $wp_user = wp_get_current_user();
        } else {
            return '';
        }

        if (!$wp_user || empty($wp_user->ID)) {
            return '';
        }

        if (self::should_skip_portal_redirect($wp_user)) {
            return '';
        }

        $user_id = (string) $wp_user->ID;
        $email = (string) $wp_user->user_email;
        if ($email === '') {
            return '';
        }

        $crm_url = rtrim(Brixen_CRM_Webhook_Sender::get_crm_url(), '/');
        $secret = Brixen_CRM_Webhook_Sender::get_webhook_secret();
        $timestamp = time();
        $payload_str = $user_id . '|' . $email . '|' . $timestamp;
        $signature = hash_hmac('sha256', $payload_str, $secret);

        return add_query_arg(
            array(
                'wordpress_user_id' => $user_id,
                'email'             => $email,
                'timestamp'         => $timestamp,
                'signature'         => $signature,
            ),
            $crm_url . '/api/v1/auth/sso'
        );
    }

    /**
     * WordPress administrators, shop managers, and website-only (Normal)
     * customers stay on the website. B2B clients SSO into the CRM portal.
     */
    private static function should_skip_portal_redirect($user) {
        if (!$user instanceof WP_User) {
            return true;
        }
        if (user_can($user, 'manage_options')) {
            return true;
        }
        if (user_can($user, 'manage_woocommerce')) {
            return true;
        }
        if (!empty($_GET['brixen_stay'])) {
            if (!headers_sent()) {
                setcookie('brixen_website_only', '1', time() + (30 * DAY_IN_SECONDS), COOKIEPATH ? COOKIEPATH : '/', COOKIE_DOMAIN, is_ssl(), true);
            }
            return true;
        }
        if (!empty($_COOKIE['brixen_website_only'])) {
            return true;
        }
        $client_type = strtoupper((string) get_user_meta($user->ID, '_brixen_client_type', true));
        if ($client_type === 'NORMAL' || $client_type === 'WEBSITE') {
            return true;
        }
        return false;
    }

    public static function filter_login_redirect($redirect_to, $requested_redirect_to, $user) {
        if (is_wp_error($user) || !$user instanceof WP_User) {
            return $redirect_to;
        }
        $sso = self::build_url($user);
        return $sso ? $sso : $redirect_to;
    }

    public static function filter_wc_login_redirect($redirect, $user) {
        if (!$user instanceof WP_User) {
            return $redirect;
        }
        $sso = self::build_url($user);
        return $sso ? $sso : $redirect;
    }

    /**
     * Replace WooCommerce My Account with the custom Brixen client portal.
     */
    public static function maybe_redirect_my_account() {
        if (!function_exists('is_account_page') || !is_account_page()) {
            return;
        }
        if (!is_user_logged_in() || is_admin()) {
            return;
        }

        global $wp;
        if (is_array($wp) && isset($wp->query_vars['customer-logout'])) {
            return;
        }

        $sso = self::build_url();
        if ($sso === '') {
            return;
        }

        wp_redirect($sso, 302);
        exit;
    }
}

/**
 * Helper for theme / formation plugin header menus.
 *
 * @return string Signed portal SSO URL, or empty string.
 */
function brixen_crm_portal_sso_url($user = null) {
    return Brixen_CRM_Portal_SSO::build_url($user);
}

/**
 * Guest login URL for header menus → website client panel.
 */
function brixen_crm_portal_login_url() {
    return Brixen_CRM_Portal_SSO::website_client_panel_url();
}
