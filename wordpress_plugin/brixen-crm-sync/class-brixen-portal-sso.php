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
     * WordPress administrators and shop managers stay on the website admin flow.
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
