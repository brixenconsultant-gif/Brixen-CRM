<?php
/**
 * Plugin Name: Brixen Keep WordPress Admin Login
 * Description: Keeps wp-login.php for staff/admin, and sends guest storefront Log in links to the Brixen client portal.
 * Version: 1.0.1
 * Author: Brixen Consultants
 *
 * Install as a normal plugin under wp-content/plugins/brixen-keep-wp-login/
 * (Do not also place this file in mu-plugins — duplicate load causes a fatal error.)
 */

if (!defined('ABSPATH')) {
    exit;
}

/**
 * True when the current request is the real WordPress login or admin bootstrap.
 */
if (!function_exists('brixen_is_wp_login_or_admin_request')) {
    function brixen_is_wp_login_or_admin_request() {
        $uri = isset($_SERVER['REQUEST_URI']) ? (string) $_SERVER['REQUEST_URI'] : '';
        $script = isset($_SERVER['SCRIPT_NAME']) ? (string) $_SERVER['SCRIPT_NAME'] : '';
        $path = wp_parse_url($uri, PHP_URL_PATH);
        $path = is_string($path) ? strtolower($path) : '';
        $script = strtolower($script);

        if (strpos($path, 'wp-login.php') !== false || strpos($script, 'wp-login.php') !== false) {
            return true;
        }
        if (strpos($path, '/wp-admin') !== false || strpos($script, '/wp-admin') !== false) {
            return true;
        }
        if (defined('WP_ADMIN') && WP_ADMIN) {
            return true;
        }
        if (function_exists('is_admin') && is_admin()) {
            return true;
        }
        return false;
    }
}

/**
 * True when a redirect target is the customer account / client panel (not WP admin).
 */
if (!function_exists('brixen_is_client_panel_url')) {
    function brixen_is_client_panel_url($url) {
        $url = strtolower((string) $url);
        if ($url === '') {
            return false;
        }
        if (strpos($url, 'client-panel') !== false) {
            return true;
        }
        if (strpos($url, 'my-account') !== false) {
            return true;
        }
        if (function_exists('wc_get_page_permalink')) {
            $my = wc_get_page_permalink('myaccount');
            if ($my) {
                $my_path = wp_parse_url($my, PHP_URL_PATH);
                $target_path = wp_parse_url($url, PHP_URL_PATH);
                if ($my_path && $target_path && untrailingslashit($my_path) === untrailingslashit($target_path)) {
                    return true;
                }
            }
        }
        return false;
    }
}

if (!defined('BRIXEN_KEEP_WP_LOGIN_HOOKS')) {
    define('BRIXEN_KEEP_WP_LOGIN_HOOKS', true);

    /**
     * Cancel redirects that send staff away from wp-login.php to Client Panel.
     */
    add_filter('wp_redirect', function ($location, $status = 302) {
        if (!brixen_is_wp_login_or_admin_request()) {
            return $location;
        }
        if (brixen_is_client_panel_url($location)) {
            // Stay on the WordPress login form (do not send admins to /client-panel/).
            return false;
        }
        return $location;
    }, 0, 2);

    /**
     * Keep wp-login.php only for WordPress admin flows.
     * Guest storefront “Log in” goes to the website client panel.
     */
    add_filter('login_url', function ($login_url, $redirect = '', $force_reauth = false) {
        $redirect_to = is_string($redirect) ? $redirect : '';
        if ($redirect_to !== '' && (strpos($redirect_to, 'wp-admin') !== false || preg_match('#/(wp-)?admin(/|$)#', $redirect_to))) {
            return site_url('wp-login.php', 'login');
        }
        if (brixen_is_wp_login_or_admin_request()) {
            return site_url('wp-login.php', 'login');
        }
        $panel = home_url('/client-panel/');
        if (function_exists('brixen_crm_portal_login_url')) {
            $panel = brixen_crm_portal_login_url();
        } elseif (function_exists('wc_get_page_permalink')) {
            $account = wc_get_page_permalink('myaccount');
            if ($account) {
                $panel = $account;
            }
        }
        // Absolute ban on CRM host for storefront login links.
        $host = strtolower((string) wp_parse_url($panel, PHP_URL_HOST));
        if ($host !== '' && strpos($host, 'portal.brixenconsultants.com') !== false) {
            return home_url('/client-panel/');
        }
        return $panel;
    }, 999, 3);

    /**
     * After a successful login, never send administrators / shop managers to Client Panel.
     */
    add_filter('login_redirect', function ($redirect_to, $requested_redirect_to, $user) {
        if (is_wp_error($user) || !($user instanceof WP_User)) {
            return $redirect_to;
        }
        if (user_can($user, 'manage_options') || user_can($user, 'manage_woocommerce') || user_can($user, 'edit_posts')) {
            if (brixen_is_client_panel_url($redirect_to) || brixen_is_client_panel_url($requested_redirect_to)) {
                return admin_url();
            }
            if ($requested_redirect_to) {
                return $requested_redirect_to;
            }
            return admin_url();
        }
        return $redirect_to;
    }, 999, 3);

    add_filter('woocommerce_login_redirect', function ($redirect, $user) {
        if ($user instanceof WP_User && (user_can($user, 'manage_options') || user_can($user, 'manage_woocommerce') || user_can($user, 'edit_posts'))) {
            return admin_url();
        }
        return $redirect;
    }, 999, 2);
}
