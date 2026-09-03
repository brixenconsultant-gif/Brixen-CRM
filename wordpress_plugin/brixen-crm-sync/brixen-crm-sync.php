<?php
/**
 * Plugin Name: Brixen Consultants CRM & WooCommerce Sync
 * Plugin URI: https://brixenconsultants.com
 * Description: Official integration plugin for Brixen Consultants connecting WordPress User Registration and WooCommerce Checkout to the Brixen CRM & Client Portal.
 * Version: 1.6.0
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
require_once plugin_dir_path(__FILE__) . 'class-brixen-client-documents.php';
require_once plugin_dir_path(__FILE__) . 'class-brixen-client-dashboard.php';
require_once plugin_dir_path(__FILE__) . 'class-brixen-portal-sso.php';

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
        add_action('admin_post_brixen_sync_orders', array($this, 'handle_sync_orders'));
        add_action('rest_api_init', array($this, 'register_rest_routes'));
        add_action('admin_post_brixen_crm_pull_order', array($this, 'handle_pull_order'));
        add_action('admin_post_nopriv_brixen_crm_pull_order', array($this, 'handle_pull_order'));
        add_action('admin_post_brixen_sync_products', array($this, 'handle_sync_products'));
        add_action('woocommerce_update_product', array($this, 'on_product_saved'), 20, 1);
        add_action('woocommerce_new_product', array($this, 'on_product_saved'), 20, 1);

        # Shortcode for Client Portal SSO Login Button
        add_shortcode('brixen_client_portal_button', array($this, 'render_portal_sso_button'));

        add_action('template_redirect', array($this, 'serve_brand_icons'), 0);
        add_action('init', array($this, 'ensure_brand_icon_files'), 1);

        // Signed SSO: website login and /client-panel/ → portal.brixenconsultants.com
        Brixen_CRM_Portal_SSO::init();
        // Legacy WC documents tab (shortcode still works; account pages redirect to portal)
        Brixen_CRM_Client_Documents::init();
    }

    /**
     * Serve the square Brixen icon at well-known paths so mail apps can
     * show it as the sender avatar for notifications@brixenconsultants.com.
     */
    public function serve_brand_icons() {
        $path = isset($_SERVER['REQUEST_URI']) ? (string) wp_parse_url($_SERVER['REQUEST_URI'], PHP_URL_PATH) : '';
        $path = strtolower(untrailingslashit($path));
        $icons = array(
            '/apple-touch-icon.png',
            '/apple-touch-icon-precomposed.png',
            '/apple-touch-icon-180x180.png',
        );
        if (!in_array($path, $icons, true)) {
            return;
        }
        wp_redirect('https://brixenconsultants.com/wp-content/uploads/2025/11/Brixen-Consultants.png', 301);
        exit;
    }

    /**
     * Copy the square website icon into the WordPress web root so mail apps
     * and browsers can fetch /apple-touch-icon.png and /favicon.ico.
     */
    public function ensure_brand_icon_files() {
        if (get_transient('brixen_brand_icons_ready')) {
            return;
        }
        $src = 'https://brixenconsultants.com/wp-content/uploads/2025/11/Brixen-Consultants.png';
        $response = wp_remote_get($src, array('timeout' => 15, 'sslverify' => true));
        if (is_wp_error($response) || (int) wp_remote_retrieve_response_code($response) !== 200) {
            return;
        }
        $body = wp_remote_retrieve_body($response);
        if (!is_string($body) || strlen($body) < 1000) {
            return;
        }
        $names = array(
            'apple-touch-icon.png',
            'apple-touch-icon-precomposed.png',
            'favicon.png',
            'favicon.ico',
        );
        $wrote = true;
        foreach ($names as $name) {
            $dest = ABSPATH . $name;
            if (file_exists($dest) && filesize($dest) >= 1000) {
                continue;
            }
            if (file_put_contents($dest, $body) === false) {
                $wrote = false;
            }
        }
        if ($wrote) {
            set_transient('brixen_brand_icons_ready', 1, DAY_IN_SECONDS);
        }
    }

    /**
     * Handle WordPress User Registration Event
     */
    private static function extract_user_profile_fields($user_id) {
        $phone_keys = array('billing_phone', 'phone', 'mobile', 'contact_number', 'uk_contact_number', 'telephone');
        $dob_keys = array('date_of_birth', 'dob', 'birth_date', 'birthdate', 'user_registration_date_of_birth');
        $phone = '';
        foreach ($phone_keys as $key) {
            $value = trim((string) get_user_meta($user_id, $key, true));
            if ($value !== '') {
                $phone = $value;
                break;
            }
        }
        $dob = '';
        foreach ($dob_keys as $key) {
            $value = trim((string) get_user_meta($user_id, $key, true));
            if ($value !== '') {
                $dob = $value;
                break;
            }
        }
        return array('phone' => $phone, 'date_of_birth' => $dob);
    }

    public function on_user_registered($user_id) {
        $user = get_userdata($user_id);
        if (!$user) return;

        $profile = self::extract_user_profile_fields($user_id);
        $data = array(
            'wordpress_user_id' => (string) $user_id,
            'email'             => $user->user_email,
            'first_name'        => get_user_meta($user_id, 'first_name', true),
            'last_name'         => get_user_meta($user_id, 'last_name', true),
            'full_name'         => $user->display_name ? $user->display_name : $user->user_email,
            'phone'             => $profile['phone'],
            'date_of_birth'     => $profile['date_of_birth'],
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

        $profile = self::extract_user_profile_fields($user_id);
        $data = array(
            'wordpress_user_id' => (string) $user_id,
            'email'             => $user->user_email,
            'first_name'        => get_user_meta($user_id, 'first_name', true),
            'last_name'         => get_user_meta($user_id, 'last_name', true),
            'full_name'         => $user->display_name ? $user->display_name : $user->user_email,
            'phone'             => $profile['phone'],
            'date_of_birth'     => $profile['date_of_birth'],
            'country'           => get_user_meta($user_id, 'billing_country', true) ?: 'United Kingdom',
        );

        Brixen_CRM_Webhook_Sender::send_event('user.updated', $data);
    }

    public function register_rest_routes() {
        register_rest_route('brixen-crm/v1', '/orders/(?P<id>\d+)', array(
            'methods'             => 'GET',
            'callback'            => array($this, 'rest_get_order_payload'),
            'permission_callback' => array($this, 'rest_verify_order_pull_request'),
            'args'                => array(
                'id' => array(
                    'required'          => true,
                    'validate_callback' => function($value) {
                        return is_numeric($value) && (int) $value > 0;
                    },
                ),
                'timestamp' => array(
                    'required'          => true,
                    'validate_callback' => function($value) {
                        return is_numeric($value);
                    },
                ),
                'signature' => array(
                    'required' => true,
                ),
            ),
        ));
    }

    public function rest_verify_order_pull_request($request) {
        return Brixen_CRM_Webhook_Sender::verify_order_pull_request(
            $request->get_param('id'),
            $request->get_param('timestamp'),
            $request->get_param('signature')
        );
    }

    public function rest_get_order_payload($request) {
        $payload = self::pull_order_payload_for_crm((int) $request->get_param('id'), false);
        if (is_wp_error($payload)) {
            return $payload;
        }
        return rest_ensure_response(array(
            'status' => 'success',
            'data'   => $payload,
        ));
    }

    public function handle_pull_order() {
        $order_id = absint($_GET['order_id'] ?? 0);
        $timestamp = sanitize_text_field(wp_unslash($_GET['timestamp'] ?? ''));
        $signature = sanitize_text_field(wp_unslash($_GET['signature'] ?? ''));
        if (!Brixen_CRM_Webhook_Sender::verify_order_pull_request($order_id, $timestamp, $signature)) {
            wp_send_json(array('status' => 'error', 'message' => 'Unauthorized'), 401);
        }
        $payload = self::pull_order_payload_for_crm($order_id, true);
        if (is_wp_error($payload)) {
            wp_send_json(array(
                'status'  => 'error',
                'message' => $payload->get_error_message(),
            ), (int) ($payload->get_error_data()['status'] ?? 404));
        }
        wp_send_json(array(
            'status' => 'success',
            'data'   => $payload,
        ));
    }

    private static function pull_order_payload_for_crm($order_id, $send_webhook = false) {
        if (!function_exists('wc_get_order')) {
            return new WP_Error('woocommerce_missing', 'WooCommerce is not available.', array('status' => 500));
        }
        $order = wc_get_order((int) $order_id);
        if (!$order) {
            return new WP_Error('order_not_found', 'Order not found.', array('status' => 404));
        }
        $payload = self::build_order_payload($order);
        if ($send_webhook) {
            Brixen_CRM_Webhook_Sender::send_event('order.updated', $payload);
        }
        return $payload;
    }

    public static function extract_order_company_name($order) {
        $candidates = array();
        $billing = $order->get_billing_company();
        if ($billing) {
            $candidates[] = $billing;
        }
        if (method_exists($order, 'get_meta_data')) {
            foreach ($order->get_meta_data() as $meta_item) {
                $data = $meta_item->get_data();
                $key = strtolower((string) ($data['key'] ?? ''));
                $value = $data['value'] ?? '';
                if (!is_string($value) || !trim($value)) {
                    continue;
                }
                if (strpos($key, 'company') !== false || strpos($key, 'proposed') !== false || strpos($key, 'ltd') !== false) {
                    $candidates[] = trim($value);
                }
            }
        }
        foreach ($order->get_items() as $item) {
            foreach ($item->get_meta_data() as $meta_item) {
                $data = $meta_item->get_data();
                $key = strtolower((string) ($data['key'] ?? ''));
                $value = $data['value'] ?? '';
                if (!is_string($value) || !trim($value)) {
                    continue;
                }
                if (strpos($key, 'company') !== false || strpos($key, 'proposed') !== false || strpos($key, 'name') !== false) {
                    $candidates[] = trim($value);
                }
            }
        }
        foreach ($candidates as $name) {
            if ($name && strcasecmp($name, 'United Kingdom') !== 0) {
                return $name;
            }
        }
        return $billing ? $billing : '';
    }

    /**
     * Detect checkout / product file uploads on an order (generic across common plugins).
     *
     * @param WC_Order $order
     * @return array[] List of { name, url, source }
     */
    public static function extract_order_attachments($order) {
        $found = array();
        $seen = array();

        $push = function ($url, $name = '') use (&$found, &$seen) {
            $url = is_string($url) ? trim($url) : '';
            if ($url === '' || !preg_match('#^https?://#i', $url)) {
                return;
            }
            $key = strtolower($url);
            if (isset($seen[$key])) {
                return;
            }
            $path = (string) (parse_url($url, PHP_URL_PATH) ?: '');
            $ext = strtolower(pathinfo($path, PATHINFO_EXTENSION));
            $allowed = array('pdf', 'png', 'jpg', 'jpeg', 'doc', 'docx', 'zip', 'gif', 'webp');
            if ($ext && !in_array($ext, $allowed, true)) {
                return;
            }
            $seen[$key] = true;
            $basename = $name ? $name : ($path ? basename($path) : 'checkout-upload');
            if ($basename === '' || $basename === '/') {
                $basename = 'checkout-upload' . ($ext ? '.' . $ext : '.bin');
            }
            $found[] = array(
                'name'   => $basename,
                'url'    => $url,
                'source' => 'checkout',
            );
        };

        $looks_like_file_key = function ($key) {
            $kl = strtolower((string) $key);
            $hints = array(
                'file', 'upload', 'document', 'attachment', 'passport', 'id_', 'identity',
                'proof', 'certificate', 'scan', 'photo', 'image', 'pdf', 'media', 'wcuf',
                'thwcfe', 'tm_epo', 'addon', 'pao_',
            );
            foreach ($hints as $hint) {
                if (strpos($kl, $hint) !== false) {
                    return true;
                }
            }
            return false;
        };

        $walk = function ($value, $key = '', $force = false) use (&$walk, $push, $looks_like_file_key) {
            if (is_string($value)) {
                $trimmed = trim($value);
                if ($trimmed === '') {
                    return;
                }
                if (preg_match('#^https?://#i', $trimmed)) {
                    if ($force || $looks_like_file_key($key) || preg_match('#\.(pdf|png|jpe?g|docx?|zip|gif|webp)(\?|$)#i', $trimmed)) {
                        $push($trimmed);
                    }
                    return;
                }
                if (ctype_digit($trimmed) && ($force || $looks_like_file_key($key)) && function_exists('wp_get_attachment_url')) {
                    $att_id = (int) $trimmed;
                    $url = wp_get_attachment_url($att_id);
                    if ($url) {
                        $title = function_exists('get_the_title') ? get_the_title($att_id) : '';
                        $push($url, $title ? $title : basename((string) parse_url($url, PHP_URL_PATH)));
                    }
                }
                return;
            }
            if (is_int($value) || is_float($value)) {
                if (($force || $looks_like_file_key($key)) && function_exists('wp_get_attachment_url')) {
                    $att_id = (int) $value;
                    if ($att_id > 0) {
                        $url = wp_get_attachment_url($att_id);
                        if ($url) {
                            $push($url, basename((string) parse_url($url, PHP_URL_PATH)));
                        }
                    }
                }
                return;
            }
            if (!is_array($value)) {
                return;
            }
            $force_child = $force || $looks_like_file_key($key);
            foreach (array('url', 'file', 'file_url', 'link', 'path', 'src') as $url_key) {
                if (!empty($value[$url_key]) && is_string($value[$url_key])) {
                    $name = '';
                    if (!empty($value['name']) && is_string($value['name'])) {
                        $name = $value['name'];
                    } elseif (!empty($value['file_name']) && is_string($value['file_name'])) {
                        $name = $value['file_name'];
                    }
                    $push($value[$url_key], $name);
                }
            }
            foreach (array('attachment_id', 'id', 'ID') as $id_key) {
                if (isset($value[$id_key]) && (is_numeric($value[$id_key]) || (is_string($value[$id_key]) && ctype_digit($value[$id_key])))) {
                    $walk((string) $value[$id_key], $id_key, true);
                }
            }
            foreach ($value as $child_key => $child_val) {
                $walk($child_val, is_string($child_key) ? $child_key : $key, $force_child);
            }
        };

        if (method_exists($order, 'get_meta_data')) {
            foreach ($order->get_meta_data() as $meta_item) {
                $data = $meta_item->get_data();
                $key = (string) ($data['key'] ?? '');
                $walk($data['value'] ?? '', $key, $looks_like_file_key($key));
            }
        }
        foreach ($order->get_items() as $item) {
            foreach ($item->get_meta_data() as $meta_item) {
                $data = $meta_item->get_data();
                $key = (string) ($data['key'] ?? '');
                $walk($data['value'] ?? '', $key, $looks_like_file_key($key));
            }
        }

        return $found;
    }

    private static function is_plausible_uk_phone($value) {
        $text = trim((string) $value);
        if ($text === '') {
            return false;
        }
        $lower = strtolower($text);
        if (preg_match('/\.(jpe?g|png|pdf|gif|webp|docx?|zip|bmp|svg)\b/', $lower)) {
            return false;
        }
        if (strpos($text, '@') !== false || preg_match('#https?://#i', $text)) {
            return false;
        }
        if (preg_match('/(photo|image|upload|document|attachment|proof|passport|cnic|filename|\.jpg|\.pdf)/i', $lower)) {
            return false;
        }
        $digits = preg_replace('/\D/', '', $text);
        $len = strlen($digits);
        if ($len < 10 || $len > 13) {
            return false;
        }
        if (strpos($digits, '44') === 0) {
            $national = substr($digits, 2);
            return strlen($national) === 10 && in_array($national[0], array('1', '2', '3', '7', '8', '9'), true);
        }
        if ($digits[0] === '0' && $len === 11) {
            return in_array($digits[1], array('1', '2', '3', '7', '8', '9'), true);
        }
        return $len === 10 && in_array($digits[0], array('1', '2', '3', '7', '8', '9'), true);
    }

    private static function format_uk_phone($value) {
        if (!self::is_plausible_uk_phone($value)) {
            return '';
        }
        $digits = preg_replace('/\D/', '', (string) $value);
        if (strpos($digits, '44') === 0) {
            $national = substr($digits, 2);
        } elseif ($digits[0] === '0') {
            $national = substr($digits, 1);
        } else {
            $national = $digits;
        }
        if (strlen($national) > 10) {
            $national = substr($national, -10);
        }
        if (strlen($national) === 10) {
            return '+44 ' . substr($national, 0, 4) . ' ' . substr($national, 4);
        }
        return '+44 ' . $national;
    }

    private static function extract_order_phone($order) {
        $candidates = array();
        if (method_exists($order, 'get_billing_phone')) {
            $candidates[] = $order->get_billing_phone();
        }
        if (method_exists($order, 'get_shipping_phone')) {
            $candidates[] = $order->get_shipping_phone();
        }
        $customer_id = $order->get_customer_id();
        if ($customer_id) {
            $candidates[] = get_user_meta($customer_id, 'billing_phone', true);
            $candidates[] = get_user_meta($customer_id, 'phone', true);
        }
        if (method_exists($order, 'get_meta_data')) {
            foreach ($order->get_meta_data() as $meta_item) {
                $data = $meta_item->get_data();
                $key = strtolower((string) ($data['key'] ?? ''));
                $value = trim((string) ($data['value'] ?? ''));
                if ($value === '') {
                    continue;
                }
                if (preg_match('/photo|image|upload|attachment|proof|passport|file|url|name/', $key)) {
                    continue;
                }
                if (preg_match('/phone|mobile|tel|contact|whatsapp/', $key)) {
                    $candidates[] = $value;
                }
            }
        }
        foreach ($order->get_items() as $item) {
            foreach ($item->get_meta_data() as $meta_item) {
                $data = $meta_item->get_data();
                $key = strtolower((string) ($data['key'] ?? ''));
                $value = trim((string) ($data['value'] ?? ''));
                if ($value === '') {
                    continue;
                }
                if (preg_match('/photo|image|upload|attachment|proof|passport|file|url|name/', $key)) {
                    continue;
                }
                if (preg_match('/phone|mobile|tel|contact|whatsapp/', $key)) {
                    $candidates[] = $value;
                }
            }
        }
        foreach ($candidates as $raw) {
            $formatted = self::format_uk_phone($raw);
            if ($formatted !== '') {
                return $formatted;
            }
        }
        return '';
    }

    public static function build_order_payload($order) {
        $items = array();
        $item_names = array();
        foreach ($order->get_items() as $item) {
            $product = $item->get_product();
            $product_id = $item->get_product_id();
            $variation_id = $item->get_variation_id();
            $sku = ($product && method_exists($product, 'get_sku')) ? $product->get_sku() : '';
            $cats = array();
            if ($product_id && function_exists('get_the_terms')) {
                $terms = get_the_terms($product_id, 'product_cat');
                if ($terms && !is_wp_error($terms)) {
                    foreach ($terms as $term) {
                        $cats[] = array(
                            'id'   => $term->term_id,
                            'name' => $term->name,
                        );
                    }
                }
            }
            $qty = (int) $item->get_quantity();
            $line_total = (float) $item->get_total();
            $unit_price = $qty > 0 ? round($line_total / $qty, 2) : $line_total;
            $name = $item->get_name();
            $item_names[] = $name;
            $item_meta = array();
            foreach ($item->get_meta_data() as $meta_item) {
                $meta = $meta_item->get_data();
                $key = (string) ($meta['key'] ?? '');
                $value = $meta['value'] ?? '';
                if ($key !== '' && is_scalar($value) && trim((string) $value) !== '') {
                    $item_meta[$key] = (string) $value;
                }
            }
            $items[] = array(
                'product_id'    => $product_id ? (string) $product_id : null,
                'variation_id'  => $variation_id ? (string) $variation_id : null,
                'sku'           => $sku,
                'product_name'  => $name,
                'quantity'      => $qty,
                'unit_price'    => $unit_price,
                'line_total'    => $line_total,
                'categories'    => $cats,
                'category'      => !empty($cats) ? $cats[0]['name'] : null,
                'category_id'   => !empty($cats) ? $cats[0]['id'] : null,
                'item_meta'     => $item_meta,
            );
        }
        $checkout_meta = array();
        if (method_exists($order, 'get_meta_data')) {
            foreach ($order->get_meta_data() as $meta_item) {
                $data = $meta_item->get_data();
                $key = (string) ($data['key'] ?? '');
                $value = $data['value'] ?? '';
                if ($key === '' || !is_scalar($value) || trim((string) $value) === '') {
                    continue;
                }
                $kl = strtolower($key);
                if (
                    strpos($kl, '_cfs_') === 0
                    || strpos($kl, '_apff_') === 0
                    || strpos($kl, '_brixen_') === 0
                    || preg_match('/phone|mobile|tel|contact|dob|birth|director|email|role/', $kl)
                ) {
                    $checkout_meta[$key] = (string) $value;
                }
            }
        }
        $order_notes = array();
        if (function_exists('wc_get_order_notes')) {
            foreach (wc_get_order_notes(array('order_id' => $order->get_id())) as $note) {
                $content = is_object($note) && isset($note->content) ? wp_strip_all_tags($note->content) : '';
                if ($content !== '') {
                    $order_notes[] = $content;
                }
            }
        }
        return array(
            'woocommerce_order_id' => (string) $order->get_id(),
            'order_number'         => '#' . $order->get_order_number(),
            'wordpress_user_id'    => (string) $order->get_customer_id(),
            'email'                => $order->get_billing_email(),
            'full_name'            => trim($order->get_billing_first_name() . ' ' . $order->get_billing_last_name()),
            'phone'                => self::extract_order_phone($order),
            'company_name'         => self::extract_order_company_name($order),
            'billing_company'      => $order->get_billing_company(),
            'billing_address_1'    => $order->get_billing_address_1(),
            'billing_city'         => $order->get_billing_city(),
            'billing_postcode'     => $order->get_billing_postcode(),
            'billing_country'      => $order->get_billing_country() ?: 'United Kingdom',
            'service_name'         => !empty($item_names) ? implode(', ', $item_names) : 'Corporate Formation Service',
            'line_items'           => $items,
            'meta'                 => $checkout_meta,
            'order_notes'          => $order_notes,
            'attachments'          => self::extract_order_attachments($order),
            'price'                => (float) $order->get_subtotal(),
            'total'                => (float) $order->get_total(),
            'currency'             => $order->get_currency(),
            'status'               => ucfirst($order->get_status()),
            'created_at'           => $order->get_date_created() ? $order->get_date_created()->date('Y-m-d H:i:s') : date('Y-m-d H:i:s'),
        );
    }

    /**
     * Handle WooCommerce Order Creation Event
     */
    public function on_order_created($order_id, $posted_data, $order) {
        if (!$order) {
            $order = wc_get_order($order_id);
        }
        if (!$order) return;
        Brixen_CRM_Webhook_Sender::send_event('order.created', self::build_order_payload($order));
    }

    /**
     * Handle WooCommerce Order Status Change Event
     */
    public function on_order_status_changed($order_id, $old_status, $new_status, $order) {
        if (!$order) {
            $order = wc_get_order($order_id);
        }
        if (!$order) return;

        $data = self::build_order_payload($order);
        $data['status'] = ucfirst($new_status);
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
                            <input type="url" name="brixen_crm_url" value="<?php echo esc_attr(get_option('brixen_crm_url', 'https://portal.brixenconsultants.com')); ?>" class="regular-text" placeholder="https://portal.brixenconsultants.com" required />
                            <p class="description">CRM endpoint and client dashboard: https://portal.brixenconsultants.com</p>
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
            <hr />
            <h2>Sync existing website companies</h2>
            <p>Send past WooCommerce company-registration orders to the CRM Company Registered cards, including the company name from checkout.</p>
            <form method="post" action="<?php echo esc_url(admin_url('admin-post.php')); ?>">
                <input type="hidden" name="action" value="brixen_sync_orders" />
                <?php wp_nonce_field('brixen_sync_orders'); ?>
                <?php submit_button('Sync existing orders to CRM', 'secondary', 'submit', false); ?>
            </form>
            <hr />
            <h2>Sync website products</h2>
            <p>Send every WooCommerce product into the CRM Services Catalog and Orders Manager product filter.</p>
            <form method="post" action="<?php echo esc_url(admin_url('admin-post.php')); ?>">
                <input type="hidden" name="action" value="brixen_sync_products" />
                <?php wp_nonce_field('brixen_sync_products'); ?>
                <?php submit_button('Sync products to CRM', 'secondary', 'submit', false); ?>
            </form>
            <?php if (isset($_GET['brixen_sync']) && $_GET['brixen_sync'] === 'ok') : ?>
                <div class="notice notice-success"><p>Existing website orders were sent to the CRM.</p></div>
            <?php elseif (isset($_GET['brixen_sync']) && $_GET['brixen_sync'] === 'err') : ?>
                <div class="notice notice-error"><p>The CRM sync could not finish. Check the CRM URL and webhook secret.</p></div>
            <?php elseif (isset($_GET['brixen_products']) && $_GET['brixen_products'] === 'ok') : ?>
                <div class="notice notice-success"><p>Website products were synced to the CRM product filter.</p></div>
            <?php elseif (isset($_GET['brixen_products']) && $_GET['brixen_products'] === 'err') : ?>
                <div class="notice notice-error"><p>Product sync could not finish. Check the CRM URL and webhook secret.</p></div>
            <?php endif; ?>
        </div>
        <?php
    }

    public function handle_sync_orders() {
        if (!current_user_can('manage_options') || !check_admin_referer('brixen_sync_orders')) {
            wp_die('Not allowed');
        }
        $result = Brixen_CRM_Bulk_Sync::sync_all_orders(500);
        $ok = is_array($result) && (isset($result['status']) && $result['status'] === 'success' || isset($result['summary']));
        wp_safe_redirect(add_query_arg('brixen_sync', $ok ? 'ok' : 'err', admin_url('admin.php?page=brixen-crm-sync')));
        exit;
    }

    public function handle_sync_products() {
        if (!current_user_can('manage_options') || !check_admin_referer('brixen_sync_products')) {
            wp_die('Not allowed');
        }
        $result = Brixen_CRM_Bulk_Sync::sync_all_products(500);
        $ok = is_array($result) && (isset($result['status']) && $result['status'] === 'success' || isset($result['summary']));
        wp_safe_redirect(add_query_arg('brixen_products', $ok ? 'ok' : 'err', admin_url('admin.php?page=brixen-crm-sync')));
        exit;
    }

    public function on_product_saved($product_id) {
        if (!$product_id || !function_exists('wc_get_product')) {
            return;
        }
        // Debounce bulk editor noise by syncing the full catalog in a lightweight way for one product.
        $product = wc_get_product($product_id);
        if (!$product) {
            return;
        }
        $cats = array();
        if (function_exists('get_the_terms')) {
            $terms = get_the_terms($product_id, 'product_cat');
            if ($terms && !is_wp_error($terms)) {
                foreach ($terms as $term) {
                    $cats[] = $term->name;
                }
            }
        }
        $payload = array(
            'products' => array(
                array(
                    'woocommerce_product_id' => (string) $product_id,
                    'name'                   => $product->get_name(),
                    'description'            => wp_strip_all_tags($product->get_short_description() ?: $product->get_description() ?: $product->get_name()),
                    'category'               => !empty($cats) ? $cats[0] : 'General',
                    'price'                  => (float) $product->get_regular_price() ?: (float) $product->get_price(),
                    'status'                 => $product->get_status(),
                    'sku'                    => $product->get_sku(),
                ),
            ),
        );
        $crm_url = rtrim(Brixen_CRM_Webhook_Sender::get_crm_url(), '/') . '/api/v1/wordpress/sync-products';
        $secret  = Brixen_CRM_Webhook_Sender::get_webhook_secret();
        $json_payload = wp_json_encode($payload);
        $signature    = 'sha256=' . hash_hmac('sha256', $json_payload, $secret);
        wp_remote_post($crm_url, array(
            'headers' => array(
                'Content-Type'       => 'application/json',
                'X-Brixen-Signature' => $signature,
            ),
            'body'      => $json_payload,
            'timeout'   => 20,
            'sslverify' => false,
            'blocking'  => false,
        ));
    }

    /**
     * SSO Portal Link Shortcode [brixen_client_portal_button]
     */
    public function render_portal_sso_button() {
        if (!is_user_logged_in()) {
            $login = wp_login_url(function_exists('wc_get_page_permalink') ? wc_get_page_permalink('myaccount') : home_url('/client-panel/'));
            return '<a href="' . esc_url($login) . '" class="button button-primary">Log In to Access Portal</a>';
        }

        $sso_url = brixen_crm_portal_sso_url();
        if ($sso_url === '') {
            return '<p class="brixen-portal-sso-unconfigured">' . esc_html__('Client portal SSO is not configured yet. Ask Brixen support to connect your website account.', 'brixen-crm-sync') . '</p>';
        }

        return '<a href="' . esc_url($sso_url) . '" class="button button-primary brixen-portal-sso-btn" style="background:#003971; color:#fff; border-radius:9999px; padding:10px 24px; font-weight:700; text-decoration:none; display:inline-block;">Access Brixen Client Portal &rarr;</a>';
    }
}

register_activation_hook(__FILE__, 'brixen_crm_sync_on_activate');
function brixen_crm_sync_on_activate() {
    add_rewrite_endpoint('customer-messages', EP_ROOT | EP_PAGES);
    flush_rewrite_rules();
    if (!class_exists('WooCommerce') || !class_exists('Brixen_CRM_Bulk_Sync')) {
        return;
    }
    Brixen_CRM_Bulk_Sync::sync_all_orders(500);
    Brixen_CRM_Bulk_Sync::sync_all_products(500);
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
