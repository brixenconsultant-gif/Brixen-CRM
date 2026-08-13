<?php
/**
 * Plugin Name: Brixen Consultants CRM & WooCommerce Sync
 * Plugin URI: https://brixenconsultants.com
 * Description: Official integration plugin for Brixen Consultants connecting WordPress User Registration and WooCommerce Checkout to the Brixen CRM & Client Portal.
 * Version: 1.0.0
 * Author: Brixen Consultants Engineering Team
 * Author URI: https://brixenconsultants.com
 * License: Proprietary
 * Text Domain: brixen-crm-sync
 *
 * @package BrixenCRMSync
 */

if (!defined('ABSPATH')) {
    exit; // Exit if accessed directly.
}

require_once plugin_dir_path(__FILE__) . 'class-brixen-webhook-sender.php';
require_once plugin_dir_path(__FILE__) . 'class-brixen-bulk-sync.php';

class Brixen_CRM_Sync_Plugin {

    /**
     * Singleton instance.
     */
    private static $instance = null;

    public static function get_instance() {
        if (null === self::$instance) {
            self::$instance = new self();
        }
        return self::$instance;
    }

    private function __construct() {
        // WordPress User Hooks
        add_action('user_register', array($this, 'on_user_registered'), 10, 1);
        add_action('profile_update', array($this, 'on_user_updated'), 10, 2);

        // WooCommerce Checkout & Order Hooks
        add_action('woocommerce_checkout_order_processed', array($this, 'on_order_created'), 10, 3);
        add_action('woocommerce_order_status_changed', array($this, 'on_order_status_changed'), 10, 4);
        add_action('woocommerce_payment_complete', array($this, 'on_payment_complete'), 10, 1);

        // WP Admin Menu & Settings
        add_action('admin_menu', array($this, 'register_admin_menu'));
        add_action('admin_init', array($this, 'register_settings'));

        // Shortcode for Client Portal SSO Login Button
        add_shortcode('brixen_client_portal_button', array($this, 'render_portal_sso_button'));
    }

    /**
     * Handle WordPress User Registration Event
     */
    public function on_user_registered($user_id) {
        $user = get_userdata($user_id);
        if (!$user) return;

        $data = array(
            'wordpress_user_id' => (string) $user_id,
            'email'             => $user->user_email,
            'first_name'        => get_user_meta($user_id, 'first_name', true),
            'last_name'         => get_user_meta($user_id, 'last_name', true),
            'full_name'         => $user->display_name ? $user->display_name : $user->user_email,
            'phone'             => get_user_meta($user_id, 'billing_phone', true),
            'country'           => get_user_meta($user_id, 'billing_country', true) ?: 'United Kingdom',
        );

        Brixen_CRM_Webhook_Sender::send_event('user.created', $data);
    }

    /**
     * Handle WordPress User Profile Update Event
     */
    public function on_user_updated($user_id, $old_user_data = null) {
        $user = get_userdata($user_id);
        if (!$user) return;

        $data = array(
            'wordpress_user_id' => (string) $user_id,
            'email'             => $user->user_email,
            'first_name'        => get_user_meta($user_id, 'first_name', true),
            'last_name'         => get_user_meta($user_id, 'last_name', true),
            'full_name'         => $user->display_name ? $user->display_name : $user->user_email,
            'phone'             => get_user_meta($user_id, 'billing_phone', true),
            'country'           => get_user_meta($user_id, 'billing_country', true) ?: 'United Kingdom',
        );

        Brixen_CRM_Webhook_Sender::send_event('user.updated', $data);
    }

    /**
     * Handle WooCommerce Order Creation Event
     */
    public function on_order_created($order_id, $posted_data, $order) {
        if (!$order) {
            $order = wc_get_order($order_id);
        }
        if (!$order) return;

        $items = array();
        foreach ($order->get_items() as $item) {
            $items[] = $item->get_name();
        }
        $service_name = !empty($items) ? implode(', ', $items) : 'Corporate Formation Service';

        $data = array(
            'woocommerce_order_id' => (string) $order_id,
            'order_number'         => '#' . $order->get_order_number(),
            'wordpress_user_id'   => (string) $order->get_customer_id(),
            'email'                => $order->get_billing_email(),
            'full_name'            => $order->get_billing_first_name() . ' ' . $order->get_billing_last_name(),
            'phone'                => $order->get_billing_phone(),
            'service_name'         => $service_name,
            'price'                => (float) $order->get_subtotal(),
            'total'                => (float) $order->get_total(),
            'currency'             => $order->get_currency(),
            'status'               => ucfirst($order->get_status()),
            'created_at'           => $order->get_date_created() ? $order->get_date_created()->date('Y-m-d H:i:s') : date('Y-m-d H:i:s'),
        );

        Brixen_CRM_Webhook_Sender::send_event('order.created', $data);
    }

    /**
     * Handle WooCommerce Order Status Change Event
     */
    public function on_order_status_changed($order_id, $old_status, $new_status, $order) {
        if (!$order) {
            $order = wc_get_order($order_id);
        }
        if (!$order) return;

        $data = array(
            'woocommerce_order_id' => (string) $order_id,
            'order_number'         => '#' . $order->get_order_number(),
            'wordpress_user_id'   => (string) $order->get_customer_id(),
            'email'                => $order->get_billing_email(),
            'status'               => ucfirst($new_status),
            'total'                => (float) $order->get_total(),
        );

        Brixen_CRM_Webhook_Sender::send_event('order.updated', $data);
    }

    /**
     * Handle WooCommerce Payment Complete Event
     */
    public function on_payment_complete($order_id) {
        $order = wc_get_order($order_id);
        if (!$order) return;

        $data = array(
            'woocommerce_order_id' => (string) $order_id,
            'order_number'         => '#' . $order->get_order_number(),
            'wordpress_user_id'   => (string) $order->get_customer_id(),
            'email'                => $order->get_billing_email(),
            'status'               => 'Processing',
            'payment_status'       => 'Paid',
            'total'                => (float) $order->get_total(),
        );

        Brixen_CRM_Webhook_Sender::send_event('payment.completed', $data);
    }

    /**
     * Register WP Admin Menu Page
     */
    public function register_admin_menu() {
        add_menu_page(
            'Brixen CRM Sync',
            'Brixen CRM',
            'manage_options',
            'brixen-crm-sync',
            array($this, 'render_admin_settings_page'),
            'dashicons-cloud',
            80
        );
    }

    /**
     * Register Plugin Settings
     */
    public function register_settings() {
        register_setting('brixen_crm_options', 'brixen_crm_url');
        register_setting('brixen_crm_options', 'brixen_crm_webhook_secret');
    }

    /**
     * Render Plugin Admin Settings Page
     */
    public function render_admin_settings_page() {
        ?>
        <div class="wrap">
            <h1>Brixen Consultants CRM & WooCommerce Integration</h1>
            <form method="post" action="options.php">
                <?php
                settings_fields('brixen_crm_options');
                do_settings_sections('brixen_crm_options');
                ?>
                <table class="form-table">
                    <tr valign="top">
                        <th scope="row">CRM Base URL</th>
                        <td>
                            <input type="url" name="brixen_crm_url" value="<?php echo esc_attr(get_option('brixen_crm_url', '')); ?>" class="regular-text" placeholder="https://portal.brixenconsultants.com" required />
                            <p class="description">Base endpoint for the Brixen Consultants CRM (e.g. https://portal.brixenconsultants.com).</p>
                        </td>
                    </tr>
                    <tr valign="top">
                        <th scope="row">Webhook Secret</th>
                        <td>
                            <input type="password" name="brixen_crm_webhook_secret" value="<?php echo esc_attr(get_option('brixen_crm_webhook_secret', '')); ?>" class="regular-text" placeholder="Enter secure webhook secret" required />
                            <p class="description">Shared secret key used to compute HMAC-SHA256 signatures for webhook verification.</p>
                        </td>
                    </tr>
                </table>
                <?php submit_button(); ?>
            </form>
        </div>
        <?php
    }

    /**
     * SSO Portal Link Shortcode [brixen_client_portal_button]
     */
    public function render_portal_sso_button() {
        if (!is_user_logged_in()) {
            return '<a href="' . esc_url(wp_login_url()) . '" class="button button-primary">Log In to Access Portal</a>';
        }

        $user = wp_get_current_user();
        $user_id = (string) $user->ID;
        $email = $user->user_email;
        $crm_url = rtrim(Brixen_CRM_Webhook_Sender::get_crm_url(), '/');
        $secret = Brixen_CRM_Webhook_Sender::get_webhook_secret();

        $timestamp = time();
        $payload_str = $user_id . '|' . $email . '|' . $timestamp;
        $signature = hash_hmac('sha256', $payload_str, $secret);

        $sso_url = add_query_arg(array(
            'wordpress_user_id' => $user_id,
            'email'             => $email,
            'timestamp'         => $timestamp,
            'signature'         => $signature,
        ), $crm_url . '/api/v1/auth/sso');

        return '<a href="' . esc_url($sso_url) . '" target="_blank" class="button button-primary brixen-portal-sso-btn" style="background:#003971; color:#fff; border-radius:9999px; padding:10px 24px; font-weight:700; text-decoration:none; display:inline-block;">Access Brixen Client Portal &rarr;</a>';
    }
}

// Defer initialization until ALL active plugins (including WooCommerce) are loaded
add_action('plugins_loaded', 'run_brixen_crm_sync_plugin');
function run_brixen_crm_sync_plugin() {
    if (!class_exists('WooCommerce')) {
        add_action('admin_notices', function() {
            echo '<div class="notice notice-warning"><p>Brixen CRM Sync requires WooCommerce to be installed and active.</p></div>';
        });
        return;
    }
    return Brixen_CRM_Sync_Plugin::get_instance();
}
