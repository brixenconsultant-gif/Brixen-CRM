<?php
/**
 * Plugin Name: Brixen Company Formation Form
 * Description: Separate company-formation file-details form for Digital, Professional and All Inclusive packages. Does not create KYC / Identity Verification orders.
 * Version: 1.1.4
 * Author: Brixen Consultants
 */

if (!defined('ABSPATH')) {
    exit;
}

function brixen_normalize_retail_price($amount) {
    $value = round((float) $amount, 2);
    if ($value <= 0) {
        return $value;
    }
    $cents = (int) round($value * 100);
    $pounds_whole = (int) $value;
    if ($cents % 10 === 9) {
        return (float) ceil($value);
    }
    if ($value == $pounds_whole && $pounds_whole % 10 === 9) {
        return (float) ($pounds_whole + 1);
    }
    return $value;
}

function brixen_format_retail_price($amount) {
    $value = brixen_normalize_retail_price($amount);
    if (abs($value - round($value)) < 0.001) {
        return (string) (int) round($value);
    }
    return number_format($value, 2, '.', '');
}

function brixen_normalize_retail_price_html($html) {
    if (!is_string($html) || $html === '') {
        return $html;
    }
    return preg_replace_callback('/£([\d,]+(?:\.\d{1,2})?)/', function ($matches) {
        $raw = str_replace(',', '', $matches[1]);
        $normalized = brixen_normalize_retail_price((float) $raw);
        return '£' . brixen_format_retail_price($normalized);
    }, $html);
}

class Brixen_Company_Formation_Form {

    const SESSION_KEY = 'cfs_form_data';
    const DRAFT_KEY = 'cfs_form_draft';
    const USER_META_KEY = '_cfs_form_draft';
    const KYC_PRODUCT_IDS = array(15145, 790);
    const ALLOWED_PRODUCTS = array(
        13990 => 'Digital Package',
        14057 => 'Professional Package',
        14233 => 'All Inclusive Package',
    );

    private static $instance = null;

    public static function get_instance() {
        if (null === self::$instance) {
            self::$instance = new self();
        }
        return self::$instance;
    }

    private function __construct() {
        add_shortcode('company_formation_form', array($this, 'form_shortcode'));
        add_action('wp_enqueue_scripts', array($this, 'enqueue_assets'));
        add_action('wp_ajax_cfs_process_form', array($this, 'process_form'));
        add_action('wp_ajax_nopriv_cfs_process_form', array($this, 'process_form'));
        add_action('wp_ajax_cfs_save_draft', array($this, 'save_draft'));
        add_action('wp_ajax_nopriv_cfs_save_draft', array($this, 'save_draft'));
        add_filter('woocommerce_persistent_cart_enabled', '__return_true');
        add_filter('wc_session_expiring', array($this, 'session_expiring'));
        add_filter('wc_session_expiration', array($this, 'session_expiration'));
        add_filter('woocommerce_get_cart_item_from_session', array($this, 'restore_cart_item_from_session'), 10, 2);
        add_action('woocommerce_cart_loaded_from_session', array($this, 'restore_session_from_cart'));
        add_action('template_redirect', array($this, 'restore_unpaid_orders_to_cart'), 5);
        add_action('wp_login', array($this, 'reset_unpaid_restore_flag'), 10, 2);
        add_action('woocommerce_payment_complete', array($this, 'clear_saved_form_after_payment'));
        add_action('woocommerce_order_status_changed', array($this, 'maybe_clear_saved_form_on_status'), 10, 3);
        add_action('wp_ajax_cfs_search_sic', array($this, 'search_sic'));
        add_action('wp_ajax_nopriv_cfs_search_sic', array($this, 'search_sic'));
        add_action('wp_ajax_cfs_search_postcode', array($this, 'search_postcode'));
        add_action('wp_ajax_nopriv_cfs_search_postcode', array($this, 'search_postcode'));
        add_action('wp_ajax_cfs_autocomplete_postcode', array($this, 'autocomplete_postcode'));
        add_action('wp_ajax_nopriv_cfs_autocomplete_postcode', array($this, 'autocomplete_postcode'));
        add_action('woocommerce_checkout_update_order_meta', array($this, 'save_form_data_to_order'));
        add_action('woocommerce_admin_order_data_after_order_details', array($this, 'display_form_data_admin'));
        add_action('woocommerce_email_order_meta', array($this, 'add_to_order_emails'), 10, 4);
        add_filter('woocommerce_get_item_data', array($this, 'cart_item_service_label'), 10, 2);
        add_action('woocommerce_checkout_create_order_line_item', array($this, 'save_line_item_service'), 10, 4);
        add_filter('woocommerce_checkout_get_value', array($this, 'prefill_checkout_value'), 20, 2);
        add_action('woocommerce_checkout_init', array($this, 'prefill_customer_on_checkout'), 5);
        add_filter('woocommerce_checkout_fields', array($this, 'remove_checkout_state_fields'), 99);
        add_filter('woocommerce_default_address_fields', array($this, 'hide_address_state_field'), 99);
        add_filter('woocommerce_get_country_locale', array($this, 'hide_locale_state_fields'), 99);
        add_action('woocommerce_after_checkout_validation', array($this, 'ignore_checkout_state_errors'), 20, 2);
        add_action('wp_head', array($this, 'print_checkout_button_css'), 120);
        add_filter('woocommerce_product_get_price', array($this, 'filter_product_price'), 20, 2);
        add_filter('woocommerce_product_get_regular_price', array($this, 'filter_product_price'), 20, 2);
        add_filter('woocommerce_product_get_sale_price', array($this, 'filter_product_price'), 20, 2);
        add_filter('woocommerce_cart_item_price', array($this, 'filter_cart_price_html'), 20, 3);
        add_filter('woocommerce_cart_item_subtotal', array($this, 'filter_cart_price_html'), 20, 3);
        add_filter('woocommerce_cart_subtotal', array($this, 'filter_cart_price_html'), 20, 1);
        add_filter('woocommerce_cart_total', array($this, 'filter_cart_price_html'), 20, 1);
        add_filter('the_content', array($this, 'filter_pricing_html'), 25);
        add_filter('elementor/frontend/the_content', array($this, 'filter_pricing_html'), 25);
    }

    public function filter_product_price($price, $product) {
        if ($price === '' || $price === null) {
            return $price;
        }
        return (string) brixen_normalize_retail_price($price);
    }

    public function filter_cart_price_html($html) {
        return brixen_normalize_retail_price_html($html);
    }

    public function filter_pricing_html($html) {
        return brixen_normalize_retail_price_html($html);
    }

    private function order_has_formation_product($order) {
        foreach ($order->get_items() as $item) {
            if (isset(self::ALLOWED_PRODUCTS[(int) $item->get_product_id()])) {
                return true;
            }
        }
        return false;
    }

    private function order_has_kyc_product($order) {
        foreach ($order->get_items() as $item) {
            if (in_array((int) $item->get_product_id(), self::KYC_PRODUCT_IDS, true)) {
                return true;
            }
        }
        return false;
    }

    private function allowed_product_id($product_id) {
        $product_id = absint($product_id);
        return isset(self::ALLOWED_PRODUCTS[$product_id]) ? $product_id : 0;
    }

    private function get_prefilled_company_name() {
        foreach (array('company_name', 'cnc-name', 'name', 'company') as $key) {
            if (!empty($_GET[$key])) {
                return sanitize_text_field(wp_unslash($_GET[$key]));
            }
        }
        if (function_exists('WC') && WC()->cart) {
            foreach (WC()->cart->get_cart() as $item) {
                if (!empty($item['company_name'])) {
                    return sanitize_text_field($item['company_name']);
                }
            }
        }
        if (function_exists('WC') && WC()->session) {
            $saved = WC()->session->get(self::SESSION_KEY);
            if (!empty($saved['company_name'])) {
                return sanitize_text_field($saved['company_name']);
            }
        }
        return '';
    }

    private function sv($saved, $key, $default = '') {
        if (!is_array($saved) || !isset($saved[$key]) || $saved[$key] === '' || $saved[$key] === null) {
            return $default;
        }
        return $saved[$key];
    }

    private function get_saved_form() {
        $saved = array();
        if (function_exists('WC') && WC()->session) {
            $session = WC()->session->get(self::SESSION_KEY);
            if (is_array($session) && $session) {
                $saved = $session;
            } else {
                $draft = WC()->session->get(self::DRAFT_KEY);
                if (is_array($draft) && $draft) {
                    $saved = $draft;
                }
            }
        }
        if (empty($saved['director_name']) && function_exists('WC') && WC()->cart) {
            foreach (WC()->cart->get_cart() as $item) {
                if (!empty($item['cfs_form']) && is_array($item['cfs_form'])) {
                    $saved = $item['cfs_form'];
                    break;
                }
            }
        }
        if (empty($saved['director_name']) && is_user_logged_in()) {
            $meta = get_user_meta(get_current_user_id(), self::USER_META_KEY, true);
            if (is_array($meta) && $meta) {
                $saved = $meta;
            }
        }
        return is_array($saved) ? $saved : array();
    }

    private function store_draft($form_data) {
        if (function_exists('WC') && WC()->session) {
            if (!WC()->session->has_session()) {
                WC()->session->set_customer_session_cookie(true);
            }
            WC()->session->set(self::DRAFT_KEY, $form_data);
            if (!empty($form_data['director_name'])) {
                WC()->session->set(self::SESSION_KEY, $form_data);
            }
        }
        if (is_user_logged_in() && is_array($form_data)) {
            update_user_meta(get_current_user_id(), self::USER_META_KEY, $form_data);
        }
        $this->apply_form_to_customer($form_data);
    }

    private function split_person_name($full) {
        $full = trim(preg_replace('/\s+/', ' ', (string) $full));
        if ($full === '') {
            return array('', '');
        }
        $parts = explode(' ', $full);
        if (count($parts) === 1) {
            return array($parts[0], '');
        }
        $last = array_pop($parts);
        return array(implode(' ', $parts), $last);
    }

    private function country_names() {
        if (function_exists('WC') && WC()->countries) {
            return array_values(WC()->countries->get_countries());
        }
        return array('United Kingdom (UK)');
    }

    private function country_name_to_code($name) {
        $name = trim((string) $name);
        if ($name === '') {
            return '';
        }
        if (!function_exists('WC') || !WC()->countries) {
            return preg_match('/united kingdom|\buk\b/i', $name) ? 'GB' : '';
        }
        $countries = WC()->countries->get_countries();
        $upper = strtoupper($name);
        if (isset($countries[$upper])) {
            return $upper;
        }
        $norm = strtolower(preg_replace('/[^a-z]+/', '', $name));
        foreach ($countries as $code => $label) {
            if (strcasecmp($label, $name) === 0) {
                return $code;
            }
            if (strtolower(preg_replace('/[^a-z]+/', '', $label)) === $norm) {
                return $code;
            }
        }
        if (in_array($norm, array('unitedkingdom', 'unitedkingdomuk', 'greatbritain', 'england', 'uk', 'gb'), true)) {
            return 'GB';
        }
        return '';
    }

    private function checkout_map_from_form($form = null) {
        if ($form === null) {
            $form = $this->get_saved_form();
        }
        if (!is_array($form) || empty($form['director_name'])) {
            return array();
        }
        list($first, $last) = $this->split_person_name($form['director_name']);
        $code = $this->country_name_to_code($form['address_country'] ?? '');
        $map = array(
            'billing_first_name' => $first,
            'billing_last_name' => $last,
            'billing_company' => $form['company_name'] ?? '',
            'billing_email' => $form['registered_email'] ?? '',
            'billing_phone' => $form['uk_contact_number'] ?? '',
            'billing_address_1' => $form['address_street'] ?? '',
            'billing_address_2' => $form['address_line2'] ?? '',
            'billing_city' => $form['address_city'] ?? '',
            'billing_state' => $form['address_state'] ?? '',
            'billing_postcode' => $form['address_zip'] ?? '',
            'billing_country' => $code,
        );
        foreach ($map as $key => $value) {
            if (strpos($key, 'billing_') !== 0 || in_array($key, array('billing_email', 'billing_phone'), true)) {
                continue;
            }
            $map[preg_replace('/^billing_/', 'shipping_', $key)] = $value;
        }
        return array_filter($map, function ($value) {
            return $value !== '' && $value !== null;
        });
    }

    private function apply_form_to_customer($form_data) {
        if (!is_array($form_data) || !function_exists('WC')) {
            return;
        }
        if (!WC()->customer && function_exists('wc_load_cart')) {
            wc_load_cart();
        }
        if (!WC()->customer) {
            return;
        }
        $map = $this->checkout_map_from_form($form_data);
        if (!$map) {
            return;
        }
        $customer = WC()->customer;
        $setters = array(
            'billing_first_name' => 'set_billing_first_name',
            'billing_last_name' => 'set_billing_last_name',
            'billing_company' => 'set_billing_company',
            'billing_email' => 'set_billing_email',
            'billing_phone' => 'set_billing_phone',
            'billing_address_1' => 'set_billing_address_1',
            'billing_address_2' => 'set_billing_address_2',
            'billing_city' => 'set_billing_city',
            'billing_state' => 'set_billing_state',
            'billing_postcode' => 'set_billing_postcode',
            'billing_country' => 'set_billing_country',
            'shipping_first_name' => 'set_shipping_first_name',
            'shipping_last_name' => 'set_shipping_last_name',
            'shipping_company' => 'set_shipping_company',
            'shipping_address_1' => 'set_shipping_address_1',
            'shipping_address_2' => 'set_shipping_address_2',
            'shipping_city' => 'set_shipping_city',
            'shipping_state' => 'set_shipping_state',
            'shipping_postcode' => 'set_shipping_postcode',
            'shipping_country' => 'set_shipping_country',
        );
        foreach ($setters as $key => $method) {
            if (!empty($map[$key]) && method_exists($customer, $method)) {
                $customer->{$method}($map[$key]);
            }
        }
        $customer->save();
    }

    public function prefill_checkout_value($value, $input) {
        $map = $this->checkout_map_from_form();
        if (isset($map[$input]) && $map[$input] !== '') {
            return $map[$input];
        }
        return $value;
    }

    public function prefill_customer_on_checkout() {
        $this->apply_form_to_customer($this->get_saved_form());
    }

    public function remove_checkout_state_fields($fields) {
        unset($fields['billing']['billing_state']);
        unset($fields['shipping']['shipping_state']);
        return $fields;
    }

    public function hide_address_state_field($fields) {
        if (isset($fields['state'])) {
            $fields['state']['required'] = false;
            $fields['state']['hidden'] = true;
        }
        return $fields;
    }

    public function hide_locale_state_fields($locale) {
        if (!is_array($locale)) {
            return $locale;
        }
        foreach ($locale as $code => $fields) {
            if (!is_array($fields)) {
                continue;
            }
            $locale[$code]['state']['required'] = false;
            $locale[$code]['state']['hidden'] = true;
        }
        return $locale;
    }

    public function ignore_checkout_state_errors($data, $errors) {
        if (!is_wp_error($errors)) {
            return;
        }
        foreach (array('billing_state', 'shipping_state') as $key) {
            $errors->remove($key);
        }
    }

    private function collect_posted_fields($product_id) {
        $locked_name = $this->get_prefilled_company_name();
        $company_name = $locked_name !== '' ? $locked_name : sanitize_text_field(wp_unslash($_POST['company_name'] ?? ''));
        return array(
            'order_type' => 'company_formation',
            'brixen_service' => 'company_formation',
            'package_name' => sanitize_text_field(wp_unslash($_POST['cfs_package_name'] ?? self::ALLOWED_PRODUCTS[$product_id])),
            'product_id' => $product_id,
            'company_name' => $company_name,
            'director_name' => sanitize_text_field(wp_unslash($_POST['director_name'] ?? '')),
            'date_of_birth' => sanitize_text_field(wp_unslash($_POST['date_of_birth'] ?? '')),
            'passport_cnic' => sanitize_text_field(wp_unslash($_POST['passport_cnic'] ?? '')),
            'issuance_country' => sanitize_text_field(wp_unslash($_POST['issuance_country'] ?? '')),
            'registered_email' => sanitize_email(wp_unslash($_POST['registered_email'] ?? '')),
            'uk_contact_number' => sanitize_text_field(wp_unslash($_POST['uk_contact_number'] ?? '')),
            'company_role' => sanitize_text_field(wp_unslash($_POST['company_role'] ?? '')),
            'address_street' => sanitize_text_field(wp_unslash($_POST['address_street'] ?? '')),
            'address_line2' => sanitize_text_field(wp_unslash($_POST['address_line2'] ?? '')),
            'address_city' => sanitize_text_field(wp_unslash($_POST['address_city'] ?? '')),
            'address_state' => sanitize_text_field(wp_unslash($_POST['address_state'] ?? '')),
            'address_zip' => sanitize_text_field(wp_unslash($_POST['address_zip'] ?? '')),
            'address_country' => sanitize_text_field(wp_unslash($_POST['address_country'] ?? '')),
            'registered_address_street' => sanitize_text_field(wp_unslash($_POST['registered_address_street'] ?? '')),
            'registered_address_line2' => sanitize_text_field(wp_unslash($_POST['registered_address_line2'] ?? '')),
            'registered_address_city' => sanitize_text_field(wp_unslash($_POST['registered_address_city'] ?? '')),
            'registered_address_state' => sanitize_text_field(wp_unslash($_POST['registered_address_state'] ?? '')),
            'registered_address_zip' => sanitize_text_field(wp_unslash($_POST['registered_address_zip'] ?? '')),
            'registered_address_country' => 'United Kingdom',
            'sic_code' => sanitize_text_field(wp_unslash($_POST['sic_code'] ?? '')),
            'business_description' => sanitize_textarea_field(wp_unslash($_POST['business_description'] ?? '')),
            'non_refund_ack' => isset($_POST['non_refund_ack']) ? '1' : '0',
            'timestamp' => time(),
        );
    }

    public function save_draft() {
        if (!isset($_POST['cfs_form_nonce']) || !wp_verify_nonce(sanitize_text_field(wp_unslash($_POST['cfs_form_nonce'])), 'cfs_form_action')) {
            wp_send_json_error(array('message' => 'Security check failed'));
        }
        $product_id = $this->allowed_product_id($_POST['cfs_product_id'] ?? 0);
        if (!$product_id) {
            wp_send_json_error(array('message' => 'Invalid package'));
        }
        $form_data = $this->collect_posted_fields($product_id);
        $previous = $this->get_saved_form();
        if (!empty($previous['uploaded_files'])) {
            $form_data['uploaded_files'] = $previous['uploaded_files'];
        }
        $this->store_draft($form_data);
        wp_send_json_success(array('saved' => true));
    }

    public function restore_cart_item_from_session($item, $values) {
        foreach (array('cfs_form', 'company_name', 'brixen_service', 'unique_key') as $key) {
            if (isset($values[$key])) {
                $item[$key] = $values[$key];
            }
        }
        return $item;
    }

    public function session_expiring() {
        return WEEK_IN_SECONDS;
    }

    public function session_expiration() {
        return 2 * WEEK_IN_SECONDS;
    }

    public function restore_session_from_cart() {
        if (!function_exists('WC') || !WC()->session || !WC()->cart) {
            return;
        }
        $existing = WC()->session->get(self::SESSION_KEY);
        if (is_array($existing) && !empty($existing['director_name'])) {
            return;
        }
        foreach (WC()->cart->get_cart() as $item) {
            if (!empty($item['cfs_form']) && is_array($item['cfs_form'])) {
                $this->store_draft($item['cfs_form']);
                break;
            }
        }
    }

    public function reset_unpaid_restore_flag($user_login, $user) {
        if (function_exists('WC') && WC()->session) {
            WC()->session->set('cfs_unpaid_restored', null);
        }
    }

    public function restore_unpaid_orders_to_cart() {
        if (is_admin() || wp_doing_ajax() || wp_doing_cron()) {
            return;
        }
        if (!function_exists('WC') || !WC()->cart || !WC()->session) {
            return;
        }
        if (function_exists('is_checkout_pay_page') && is_checkout_pay_page()) {
            return;
        }
        if (function_exists('is_order_received_page') && is_order_received_page()) {
            return;
        }
        if (WC()->session->get('cfs_unpaid_restored')) {
            return;
        }

        $query = array(
            'status' => array('pending', 'failed', 'checkout-draft'),
            'limit' => 15,
            'orderby' => 'date',
            'order' => 'DESC',
            'return' => 'objects',
        );
        if (is_user_logged_in()) {
            $query['customer_id'] = get_current_user_id();
        } else {
            $email = '';
            if (WC()->customer) {
                $email = WC()->customer->get_billing_email();
            }
            if ($email === '') {
                $customer = WC()->session->get('customer');
                if (is_array($customer) && !empty($customer['email'])) {
                    $email = $customer['email'];
                }
            }
            $email = sanitize_email($email);
            if ($email === '') {
                WC()->session->set('cfs_unpaid_restored', 1);
                return;
            }
            $query['billing_email'] = $email;
        }

        $orders = wc_get_orders($query);
        WC()->session->set('cfs_unpaid_restored', 1);
        if (!$orders) {
            return;
        }

        $in_cart = array();
        foreach (WC()->cart->get_cart() as $item) {
            $in_cart[(int) $item['product_id']] = true;
        }

        $restored_ids = array();
        foreach ($orders as $order) {
            if (!$order instanceof WC_Order) {
                continue;
            }
            $added_any = false;
            foreach ($order->get_items() as $item) {
                $product_id = (int) $item->get_product_id();
                $variation_id = (int) $item->get_variation_id();
                if (!$product_id || isset($in_cart[$product_id])) {
                    continue;
                }
                $product = $item->get_product();
                if (!$product || !$product->exists() || !$product->is_purchasable()) {
                    continue;
                }
                $extras = array(
                    'cfs_restored_order' => $order->get_id(),
                );
                $form = $this->form_data_from_order_meta($order);
                if ($form) {
                    $extras['cfs_form'] = $form;
                    if (!empty($form['company_name'])) {
                        $extras['company_name'] = $form['company_name'];
                    }
                    if (($form['order_type'] ?? '') === 'company_formation' || !empty($form['director_name'])) {
                        $extras['brixen_service'] = 'company_formation';
                    }
                    $extras['unique_key'] = md5($product_id . ($extras['company_name'] ?? '') . $order->get_id());
                }
                $qty = max(1, (int) $item->get_quantity());
                $added = WC()->cart->add_to_cart($product_id, $qty, $variation_id, array(), $extras);
                if ($added) {
                    $in_cart[$product_id] = true;
                    $added_any = true;
                    if (!empty($extras['cfs_form'])) {
                        $this->store_draft($extras['cfs_form']);
                    }
                }
            }
            if ($added_any) {
                $restored_ids[] = $order->get_id();
            }
        }

        if (!$restored_ids) {
            return;
        }
        WC()->session->set('cfs_restored_order_ids', $restored_ids);
        if (!WC()->session->get('order_awaiting_payment')) {
            WC()->session->set('order_awaiting_payment', $restored_ids[0]);
        }
        WC()->cart->set_session();
        WC()->cart->maybe_set_cart_cookies();
    }

    private function form_data_from_order_meta($order) {
        $blob = $order->get_meta('_cfs_form_data');
        if (is_array($blob) && $blob) {
            return $blob;
        }
        $data = array();
        $files = array();
        foreach ($order->get_meta_data() as $meta) {
            $key = $meta->key;
            if (strpos($key, '_cfs_') !== 0) {
                continue;
            }
            if (in_array($key, array('_cfs_order_type', '_cfs_form_data'), true)) {
                continue;
            }
            if (preg_match('/^_cfs_(photo_id|address_proof)_(\d+)_(url|name)$/', $key, $m)) {
                $files[$m[1]][(int) $m[2]][$m[3]] = $meta->value;
                continue;
            }
            $data[substr($key, 5)] = $meta->value;
        }
        foreach ($files as $field => $rows) {
            ksort($rows);
            foreach ($rows as $row) {
                if (!empty($row['url'])) {
                    $data['uploaded_files'][$field][] = array(
                        'url' => $row['url'],
                        'name' => $row['name'] ?? '',
                    );
                }
            }
        }
        return $data;
    }

    public function maybe_clear_saved_form_on_status($order_id, $old_status, $new_status) {
        if (in_array($new_status, array('processing', 'completed'), true)) {
            $this->clear_saved_form_after_payment($order_id);
        }
    }

    public function clear_saved_form_after_payment($order_id) {
        $order = wc_get_order($order_id);
        if (!$order || !$this->order_has_formation_product($order)) {
            return;
        }
        if (function_exists('WC') && WC()->session) {
            WC()->session->set(self::SESSION_KEY, null);
            WC()->session->set(self::DRAFT_KEY, null);
            WC()->session->set('cfs_restored_order_ids', null);
            WC()->session->set('cfs_unpaid_restored', null);
        }
        $user_id = $order->get_user_id();
        if ($user_id) {
            delete_user_meta($user_id, self::USER_META_KEY);
        }
    }

    private function load_sic_codes() {
        $cached = get_transient('cfs_sic_codes_v1');
        if (is_array($cached) && $cached) {
            return $cached;
        }
        $path = plugin_dir_path(__FILE__) . 'sic-codes.json';
        if (!file_exists($path)) {
            return array();
        }
        $codes = json_decode(file_get_contents($path), true);
        if (!is_array($codes)) {
            return array();
        }
        set_transient('cfs_sic_codes_v1', $codes, DAY_IN_SECONDS);
        return $codes;
    }

    public function search_sic() {
        check_ajax_referer('cfs_sic_search', 'nonce');
        $q = strtolower(trim(sanitize_text_field(wp_unslash($_REQUEST['q'] ?? ''))));
        if (strlen($q) < 2) {
            wp_send_json_success(array());
        }
        $matches = array();
        foreach ($this->load_sic_codes() as $row) {
            $hay = strtolower(($row['code'] ?? '') . ' ' . ($row['description'] ?? '') . ' ' . ($row['section'] ?? ''));
            if (strpos($hay, $q) !== false) {
                $matches[] = array(
                    'code' => $row['code'],
                    'description' => $row['description'],
                    'section' => $row['section'] ?? '',
                );
                if (count($matches) >= 20) {
                    break;
                }
            }
        }
        wp_send_json_success($matches);
    }

    private function normalize_uk_postcode($raw) {
        $postcode = strtoupper(preg_replace('/\s+/', '', sanitize_text_field($raw)));
        if (!preg_match('/^[A-Z]{1,2}[0-9][A-Z0-9]?[0-9][A-Z]{2}$/', $postcode)) {
            return '';
        }
        return substr($postcode, 0, -3) . ' ' . substr($postcode, -3);
    }

    private function postcode_locality_address($result) {
        $postcode = $result['postcode'] ?? '';
        $district = trim((string) ($result['admin_district'] ?? ''));
        $county = trim((string) ($result['admin_county'] ?? ''));
        $region = trim((string) ($result['region'] ?? ''));
        $is_london = (strcasecmp($region, 'London') === 0);
        $city = $is_london ? 'London' : ($district !== '' ? $district : $region);
        $state = $is_london ? $district : ($county !== '' ? $county : $region);
        $parts = array_filter(array($postcode, $district, $city, $state, 'United Kingdom'));
        return array(
            'label' => implode(', ', array_unique($parts)),
            'street' => '',
            'line2' => '',
            'city' => $city,
            'state' => $state,
            'zip' => $postcode,
            'country' => 'United Kingdom',
        );
    }

    private function remote_json($url) {
        $response = wp_remote_get($url, array(
            'timeout' => 12,
            'headers' => array(
                'Accept' => 'application/json',
                'User-Agent' => 'BrixenCompanyFormation/1.0.6 (https://brixenconsultants.com)',
            ),
        ));
        if (is_wp_error($response)) {
            return null;
        }
        $code = (int) wp_remote_retrieve_response_code($response);
        $body = json_decode(wp_remote_retrieve_body($response), true);
        if ($code < 200 || $code >= 300 || !is_array($body)) {
            return null;
        }
        return $body;
    }

    public function autocomplete_postcode() {
        check_ajax_referer('cfs_postcode_search', 'nonce');
        $q = strtoupper(preg_replace('/\s+/', '', sanitize_text_field(wp_unslash($_REQUEST['q'] ?? ''))));
        if (strlen($q) < 2 || strlen($q) > 8 || !preg_match('/^[A-Z0-9]+$/', $q)) {
            wp_send_json_success(array());
        }
        $cache_key = 'cfs_pc_ac_' . md5($q);
        $cached = get_transient($cache_key);
        if (is_array($cached)) {
            wp_send_json_success($cached);
        }
        $body = $this->remote_json('https://api.postcodes.io/postcodes/' . rawurlencode($q) . '/autocomplete');
        $suggestions = array();
        if (!empty($body['result']) && is_array($body['result'])) {
            $suggestions = array_values(array_filter($body['result']));
        }
        set_transient($cache_key, $suggestions, DAY_IN_SECONDS);
        wp_send_json_success($suggestions);
    }

    public function search_postcode() {
        check_ajax_referer('cfs_postcode_search', 'nonce');
        $formatted = $this->normalize_uk_postcode(wp_unslash($_REQUEST['postcode'] ?? ''));
        if ($formatted === '') {
            wp_send_json_error(array('message' => 'Enter a valid UK postcode, for example SW1A 1AA.'));
        }
        $cache_key = 'cfs_pc_find_v3_' . md5($formatted);
        $cached = get_transient($cache_key);
        if (is_array($cached) && !empty($cached['addresses'])) {
            wp_send_json_success($cached);
        }

        $addresses = $this->lookup_premise_addresses($formatted);

        $lookup = $this->remote_json('https://api.postcodes.io/postcodes/' . rawurlencode($formatted));
        if (empty($lookup['result']) || !is_array($lookup['result'])) {
            if (!$addresses) {
                wp_send_json_error(array('message' => 'That UK postcode was not found. Check it and try again.'));
            }
        } else {
            $locality = $this->postcode_locality_address($lookup['result']);
            if ($addresses) {
                $district = trim((string) ($lookup['result']['admin_district'] ?? ''));
                $country = trim((string) ($lookup['result']['country'] ?? ''));
                foreach ($addresses as &$row) {
                    if ($row['city'] === '') {
                        $row['city'] = $locality['city'];
                    }
                    if ($row['state'] === '') {
                        if ($locality['state'] !== '' && strcasecmp($locality['state'], $row['city']) !== 0) {
                            $row['state'] = $locality['state'];
                        } elseif ($district !== '' && strcasecmp($district, $row['city']) !== 0) {
                            $row['state'] = $district;
                        } else {
                            $row['state'] = $country;
                        }
                    }
                }
                unset($row);
            } else {
                $addresses[] = $locality;
            }
        }

        $payload = array(
            'postcode' => $formatted,
            'addresses' => $addresses,
            'fill_street' => !empty($addresses[0]['street']),
        );
        if (!empty($payload['fill_street'])) {
            set_transient($cache_key, $payload, WEEK_IN_SECONDS);
        }
        wp_send_json_success($payload);
    }

    private function lookup_premise_addresses($formatted) {
        $wanted = strtoupper(preg_replace('/\s+/', '', $formatted));
        $rows = array();

        $api_key = defined('CFS_HOMEDATA_API_KEY') ? CFS_HOMEDATA_API_KEY : get_option('cfs_homedata_api_key', '');
        if (is_string($api_key) && $api_key !== '') {
            $compact = str_replace(' ', '', $formatted);
            $response = wp_remote_get('https://api.homedata.co.uk/address/postcode/' . rawurlencode($compact) . '/', array(
                'timeout' => 12,
                'headers' => array(
                    'Accept' => 'application/json',
                    'Authorization' => 'Api-Key ' . $api_key,
                    'User-Agent' => 'BrixenCompanyFormation/1.0.6 (https://brixenconsultants.com)',
                ),
            ));
            if (!is_wp_error($response) && (int) wp_remote_retrieve_response_code($response) === 200) {
                $body = json_decode(wp_remote_retrieve_body($response), true);
                if (!empty($body['addresses']) && is_array($body['addresses'])) {
                    $rows = $body['addresses'];
                }
            }
        }

        if (!$rows) {
            $body = $this->remote_json('https://homedata.co.uk/api/public/address/find?q=' . rawurlencode($formatted));
            if (!empty($body['suggestions']) && is_array($body['suggestions'])) {
                $rows = $body['suggestions'];
            }
        }

        $addresses = array();
        $seen = array();
        foreach ($rows as $row) {
            if (!is_array($row)) {
                continue;
            }
            $postcode = strtoupper(preg_replace('/\s+/', '', (string) ($row['postcode'] ?? $formatted)));
            if ($postcode !== '' && $postcode !== $wanted) {
                continue;
            }
            $street = trim((string) ($row['address_line_1'] ?? $row['address'] ?? $row['building_number'] ?? ''));
            if ($street === '' && !empty($row['building_number']) && !empty($row['street'])) {
                $street = trim($row['building_number'] . ' ' . $row['street']);
            }
            if ($street === '' && !empty($row['street'])) {
                $street = trim((string) $row['street']);
            }
            $line2 = trim((string) ($row['address_line_2'] ?? $row['sub_building'] ?? ''));
            if ($line2 !== '' && $street !== '' && stripos($street, $line2) !== false) {
                $line2 = '';
            }
            $city = trim((string) ($row['town'] ?? $row['city'] ?? ''));
            $label = trim((string) ($row['address'] ?? ''));
            if ($label === '') {
                $label = implode(', ', array_filter(array($street, $line2, $city, $formatted)));
            } elseif (stripos($label, $formatted) === false) {
                $label .= ', ' . $formatted;
            }
            $key = strtolower($label);
            if ($street === '' || isset($seen[$key])) {
                continue;
            }
            $seen[$key] = true;
            $addresses[] = array(
                'label' => $label,
                'street' => $street,
                'line2' => $line2,
                'city' => $city,
                'state' => '',
                'zip' => $formatted,
                'country' => 'United Kingdom',
            );
        }
        return $addresses;
    }

    private function countries_options($selected = 'United Kingdom') {
        $html = '<option value="">Select Country</option>';
        foreach ($this->country_names() as $name) {
            $html .= '<option value="' . esc_attr($name) . '"' . selected($selected, $name, false) . '>' . esc_html($name) . '</option>';
        }
        return $html;
    }

    private function country_combobox($name, $selected = '', $required = true) {
        $selected = (string) $selected;
        ob_start();
        ?>
        <div class="cfs-combobox">
            <input type="text" class="cfs-input cfs-country-search" value="<?php echo esc_attr($selected); ?>" placeholder="Type a country name" autocomplete="off" role="combobox" aria-autocomplete="list" <?php echo $required ? 'required' : ''; ?>>
            <input type="hidden" name="<?php echo esc_attr($name); ?>" class="cfs-country-value" value="<?php echo esc_attr($selected); ?>">
            <div class="cfs-country-results cfs-sic-results" hidden></div>
        </div>
        <?php
        return ob_get_clean();
    }

    public function form_shortcode($atts) {
        $atts = shortcode_atts(array(
            'product_id' => '',
            'package_name' => '',
        ), $atts, 'company_formation_form');

        $product_id = $this->allowed_product_id($atts['product_id']);
        $package_name = $atts['package_name'] !== '' ? $atts['package_name'] : (self::ALLOWED_PRODUCTS[$product_id] ?? 'Company Formation');
        if (function_exists('WC') && WC()->session && !WC()->session->has_session()) {
            WC()->session->set_customer_session_cookie(true);
        }
        if (!defined('DONOTCACHEPAGE')) {
            define('DONOTCACHEPAGE', true);
        }
        if (!headers_sent()) {
            nocache_headers();
        }
        if (function_exists('do_action')) {
            do_action('litespeed_control_set_nocache', 'brixen company formation form');
        }
        $saved = $this->get_saved_form();
        $company_name = $this->get_prefilled_company_name();
        if ($company_name === '') {
            $company_name = $this->sv($saved, 'company_name');
        }
        $company_locked = $this->get_prefilled_company_name() !== '';
        $role = $this->sv($saved, 'company_role');
        $sic_code = $this->sv($saved, 'sic_code');
        $sic_desc = $this->sv($saved, 'business_description');
        $has_photo = !empty($saved['uploaded_files']['photo_id']);
        $has_proof = !empty($saved['uploaded_files']['address_proof']);

        ob_start();
        ?>
        <div class="cfs-form-container" id="ivs-form-wrapper">
            <form id="cfs-formation-form" method="post" enctype="multipart/form-data">
                <div class="cfs-form-section">
                    <h3 class="cfs-heading">Company Information</h3>
                    <div class="cfs-form-row">
                        <div class="cfs-form-col">
                            <label class="cfs-label">Company Name <span class="cfs-required">*</span></label>
                            <input type="text" name="company_name" class="cfs-input<?php echo $company_locked ? ' cfs-input-locked' : ''; ?>" value="<?php echo esc_attr($company_name); ?>" placeholder="Desired company name from your name search" required <?php echo $company_locked ? 'readonly' : ''; ?>>
                            <p class="cfs-hint"><?php echo $company_locked
                                ? 'This is filled automatically from the name you searched and cannot be changed.'
                                : 'This is filled automatically after you search and select a company name.'; ?></p>
                        </div>
                    </div>
                </div>

                <div class="cfs-form-section">
                    <h3 class="cfs-heading">Director Details</h3>
                    <div class="cfs-form-row">
                        <div class="cfs-form-col">
                            <label class="cfs-label">Director Full Name <span class="cfs-required">*</span></label>
                            <input type="text" name="director_name" class="cfs-input" placeholder="Enter director's full name" value="<?php echo esc_attr($this->sv($saved, 'director_name')); ?>" required>
                        </div>
                        <div class="cfs-form-col">
                            <label class="cfs-label">Date of Birth <span class="cfs-required">*</span></label>
                            <input type="date" name="date_of_birth" class="cfs-input" value="<?php echo esc_attr($this->sv($saved, 'date_of_birth')); ?>" required>
                        </div>
                    </div>
                    <div class="cfs-form-row">
                        <div class="cfs-form-col">
                            <label class="cfs-label">Passport/CNIC No. <span class="cfs-required">*</span></label>
                            <input type="text" name="passport_cnic" class="cfs-input" placeholder="Enter passport or CNIC number" value="<?php echo esc_attr($this->sv($saved, 'passport_cnic')); ?>" required>
                        </div>
                        <div class="cfs-form-col">
                            <label class="cfs-label">Country of issuance for your Passport/CNIC <span class="cfs-required">*</span></label>
                            <?php echo $this->country_combobox('issuance_country', $this->sv($saved, 'issuance_country', ''), true); ?>
                        </div>
                    </div>
                    <div class="cfs-form-row">
                        <div class="cfs-form-col">
                            <label class="cfs-label">Registered Email <span class="cfs-required">*</span></label>
                            <input type="email" name="registered_email" class="cfs-input" placeholder="Enter your registered email" value="<?php echo esc_attr($this->sv($saved, 'registered_email')); ?>" required>
                        </div>
                        <div class="cfs-form-col">
                            <label class="cfs-label">UK Contact Number <span class="cfs-required">*</span></label>
                            <input type="tel" name="uk_contact_number" class="cfs-input" placeholder="e.g. 07700 900123" value="<?php echo esc_attr($this->sv($saved, 'uk_contact_number')); ?>" required>
                            <p class="cfs-hint">Your UK mobile or landline number including area code.</p>
                        </div>
                    </div>
                </div>

                <div class="cfs-form-section">
                    <h3 class="cfs-heading">Role in Company <span class="cfs-required">*</span></h3>
                    <div class="cfs-radio-group">
                        <label class="cfs-radio-label"><input type="radio" name="company_role" value="director" required <?php checked($role, 'director'); ?>> <span>Director</span></label>
                        <label class="cfs-radio-label"><input type="radio" name="company_role" value="psc" required <?php checked($role, 'psc'); ?>> <span>PSC (Person with Significant control)</span></label>
                        <label class="cfs-radio-label"><input type="radio" name="company_role" value="both" required <?php checked($role, 'both'); ?>> <span>Director &amp; PSC Both</span></label>
                    </div>
                </div>

                <div class="cfs-form-section">
                    <h3 class="cfs-heading">Director Home Address <span class="cfs-required">*</span></h3>
                    <div class="cfs-form-row">
                        <div class="cfs-form-col">
                            <label class="cfs-label">Street Address <span class="cfs-required">*</span></label>
                            <input type="text" name="address_street" class="cfs-input" placeholder="Enter street address" value="<?php echo esc_attr($this->sv($saved, 'address_street')); ?>" required>
                        </div>
                    </div>
                    <div class="cfs-form-row">
                        <div class="cfs-form-col">
                            <label class="cfs-label">Address Line 2</label>
                            <input type="text" name="address_line2" class="cfs-input" placeholder="Apartment, suite, unit (optional)" value="<?php echo esc_attr($this->sv($saved, 'address_line2')); ?>">
                        </div>
                    </div>
                    <div class="cfs-form-row">
                        <div class="cfs-form-col">
                            <label class="cfs-label">City <span class="cfs-required">*</span></label>
                            <input type="text" name="address_city" class="cfs-input" value="<?php echo esc_attr($this->sv($saved, 'address_city')); ?>" required>
                        </div>
                        <div class="cfs-form-col">
                            <label class="cfs-label">Province/State <span class="cfs-required">*</span></label>
                            <input type="text" name="address_state" class="cfs-input" value="<?php echo esc_attr($this->sv($saved, 'address_state')); ?>" required>
                        </div>
                    </div>
                    <div class="cfs-form-row">
                        <div class="cfs-form-col">
                            <label class="cfs-label">Zip/Postal Code <span class="cfs-required">*</span></label>
                            <input type="text" name="address_zip" class="cfs-input" value="<?php echo esc_attr($this->sv($saved, 'address_zip')); ?>" required>
                        </div>
                        <div class="cfs-form-col">
                            <label class="cfs-label">Country <span class="cfs-required">*</span></label>
                            <?php echo $this->country_combobox('address_country', $this->sv($saved, 'address_country', 'United Kingdom (UK)'), true); ?>
                        </div>
                    </div>
                </div>

                <div class="cfs-form-section" id="cfs-registered-address">
                    <h3 class="cfs-heading">Registered Company Address <span class="cfs-required">*</span></h3>
                    <div class="cfs-form-row">
                        <div class="cfs-form-col">
                            <label class="cfs-label">UK Postcode <span class="cfs-required">*</span></label>
                            <div class="cfs-postcode-finder">
                                <input type="text" name="registered_address_zip" id="cfs-reg-postcode" class="cfs-input" placeholder="e.g. SW1A 1AA" value="<?php echo esc_attr($this->sv($saved, 'registered_address_zip')); ?>" required autocomplete="off">
                                <button type="button" class="cfs-postcode-btn" id="cfs-reg-postcode-btn">Find Address</button>
                            </div>
                            <div id="cfs-reg-postcode-suggest" class="cfs-sic-results" hidden></div>
                            <div id="cfs-reg-address-results" class="cfs-sic-results cfs-address-results" hidden></div>
                            <p id="cfs-reg-postcode-status" class="cfs-hint">Search a UK postcode to fill the registered office, or type the address below.</p>
                        </div>
                    </div>
                    <div class="cfs-form-row">
                        <div class="cfs-form-col">
                            <label class="cfs-label">Street Address <span class="cfs-required">*</span></label>
                            <input type="text" name="registered_address_street" class="cfs-input" placeholder="Enter street address" value="<?php echo esc_attr($this->sv($saved, 'registered_address_street')); ?>" required>
                        </div>
                    </div>
                    <div class="cfs-form-row">
                        <div class="cfs-form-col">
                            <label class="cfs-label">Address Line 2</label>
                            <input type="text" name="registered_address_line2" class="cfs-input" placeholder="Apartment, suite, unit (optional)" value="<?php echo esc_attr($this->sv($saved, 'registered_address_line2')); ?>">
                        </div>
                    </div>
                    <div class="cfs-form-row">
                        <div class="cfs-form-col">
                            <label class="cfs-label">City <span class="cfs-required">*</span></label>
                            <input type="text" name="registered_address_city" class="cfs-input" value="<?php echo esc_attr($this->sv($saved, 'registered_address_city')); ?>" required>
                        </div>
                        <div class="cfs-form-col">
                            <label class="cfs-label">Province/State <span class="cfs-required">*</span></label>
                            <input type="text" name="registered_address_state" class="cfs-input" value="<?php echo esc_attr($this->sv($saved, 'registered_address_state')); ?>" required>
                        </div>
                    </div>
                    <div class="cfs-form-row">
                        <div class="cfs-form-col">
                            <label class="cfs-label">Country <span class="cfs-required">*</span></label>
                            <input type="text" class="cfs-input cfs-input-locked" value="United Kingdom" readonly>
                            <input type="hidden" name="registered_address_country" value="United Kingdom">
                            <p class="cfs-hint">Registered office country is locked to the United Kingdom.</p>
                        </div>
                    </div>
                </div>

                <div class="cfs-form-section">
                    <h3 class="cfs-heading">Document Upload</h3>
                    <div class="cfs-form-row">
                        <div class="cfs-form-col">
                            <label class="cfs-label">Upload Director Photo ID <?php echo $has_photo ? '' : '<span class="cfs-required">*</span>'; ?></label>
                            <input type="file" name="photo_id[]" class="cfs-file-input" accept=".jpg,.jpeg,.png,.pdf" multiple <?php echo $has_photo ? '' : 'required'; ?>>
                            <?php if ($has_photo) : ?>
                                <p class="cfs-hint cfs-saved-files">Already saved: <?php echo esc_html(implode(', ', wp_list_pluck($saved['uploaded_files']['photo_id'], 'name'))); ?>. Upload again only if you need to replace it.</p>
                            <?php endif; ?>
                            <div class="cfs-hint">
                                <p><strong>Max. file size: 3 GB, Max. files: 5.</strong></p>
                                <p>Upload any of: Passport, National ID, Driving licence. All 4 corners must be visible. Holograms and security features must be visible.</p>
                            </div>
                        </div>
                    </div>
                    <div class="cfs-form-row">
                        <div class="cfs-form-col">
                            <label class="cfs-label">Upload Proof of Address <?php echo $has_proof ? '' : '<span class="cfs-required">*</span>'; ?></label>
                            <input type="file" name="address_proof[]" class="cfs-file-input" accept=".jpg,.jpeg,.png,.pdf" multiple <?php echo $has_proof ? '' : 'required'; ?>>
                            <?php if ($has_proof) : ?>
                                <p class="cfs-hint cfs-saved-files">Already saved: <?php echo esc_html(implode(', ', wp_list_pluck($saved['uploaded_files']['address_proof'], 'name'))); ?>. Upload again only if you need to replace it.</p>
                            <?php endif; ?>
                            <div class="cfs-hint">
                                <p><strong>Max. file size: 3 GB.</strong></p>
                                <p>The name must match the passport. Accepted: Bank statement, tax document, driving licence, national ID with address, or utility bill (water/gas/electricity/landline/broadband only, dated within last 3 months). Mobile phone bills are not accepted.</p>
                            </div>
                        </div>
                    </div>
                </div>

                <div class="cfs-form-section">
                    <h3 class="cfs-heading">Business Activity (SIC)</h3>
                    <div class="cfs-form-row">
                        <div class="cfs-form-col">
                            <label class="cfs-label">Search company activity <span class="cfs-required">*</span></label>
                            <input type="text" id="cfs-sic-search" class="cfs-input" placeholder="Type an activity, e.g. software, retail, consultancy" value="<?php echo esc_attr($sic_code !== '' ? ($sic_code . ($sic_desc !== '' ? ' — ' . $sic_desc : '')) : ''); ?>" autocomplete="off">
                            <input type="hidden" name="sic_code" id="cfs-sic-code" value="<?php echo esc_attr($sic_code); ?>" required>
                            <div id="cfs-sic-results" class="cfs-sic-results" hidden></div>
                            <p id="cfs-sic-selected" class="cfs-sic-selected"><?php echo $sic_code !== '' ? esc_html('Selected: ' . $sic_code . ($sic_desc !== '' ? ' — ' . $sic_desc : '')) : ''; ?></p>
                            <p class="cfs-hint">Official Companies House condensed SIC 2007 list. Pick the activity that best matches the company.</p>
                        </div>
                    </div>
                    <div class="cfs-form-row">
                        <div class="cfs-form-col">
                            <label class="cfs-label">Description of Business Activities</label>
                            <textarea name="business_description" id="cfs-business-description" class="cfs-input" rows="3" placeholder="Brief description of the company's business activities"><?php echo esc_textarea($sic_desc); ?></textarea>
                        </div>
                    </div>
                </div>

                <div class="cfs-form-section">
                    <label class="cfs-checkbox-label">
                        <input type="checkbox" name="non_refund_ack" value="1" required <?php checked($this->sv($saved, 'non_refund_ack'), '1'); ?>>
                        <span>I understand that company formation fees cover Companies House filing and processing. If the application cannot proceed because of invalid, false, altered, or unverifiable documents, the fee is not refundable. <span class="cfs-required">*</span></span>
                    </label>
                </div>

                <?php wp_nonce_field('cfs_form_action', 'cfs_form_nonce'); ?>
                <input type="hidden" name="cfs_product_id" value="<?php echo esc_attr($product_id); ?>">
                <input type="hidden" name="cfs_package_name" value="<?php echo esc_attr($package_name); ?>">
                <div class="cfs-submit-section">
                    <button type="button" class="cfs-add-cart-btn" id="cfs-add-to-cart">Add to cart</button>
                    <button type="submit" class="cfs-submit-btn" id="cfs-submit-form">Continue to checkout</button>
                    <p id="cfs-cart-notice" class="cfs-cart-notice" hidden></p>
                    <div id="cfs-loading" style="display:none;margin-top:10px;"><p><strong>Processing…</strong></p></div>
                </div>
            </form>
        </div>
        <?php
        return ob_get_clean();
    }

    public function enqueue_assets() {
        wp_enqueue_script('jquery');
        if (function_exists('is_checkout') && is_checkout()) {
            wp_enqueue_style('select2');
            wp_enqueue_script('selectWoo');
        }
        wp_register_style('cfs-frontend', false, array(), '1.1.4');
        wp_enqueue_style('cfs-frontend');
        wp_add_inline_style('cfs-frontend', $this->css() . $this->account_menu_css());
        wp_register_script('cfs-frontend', false, array('jquery'), '1.1.4', true);
        wp_enqueue_script('cfs-frontend');
        $account_url = function_exists('wc_get_page_permalink') ? wc_get_page_permalink('myaccount') : home_url('/client-panel/');
        $portal_url = function_exists('brixen_crm_portal_sso_url') ? brixen_crm_portal_sso_url() : '';
        $client_panel_url = ($portal_url !== '') ? $portal_url : $account_url;
        wp_localize_script('cfs-frontend', 'cfsAjax', array(
            'ajaxUrl' => admin_url('admin-ajax.php'),
            'sicNonce' => wp_create_nonce('cfs_sic_search'),
            'postcodeNonce' => wp_create_nonce('cfs_postcode_search'),
            'productId' => (string) $this->allowed_product_id($_GET['product_id'] ?? 0),
            'cartUrl' => function_exists('wc_get_cart_url') ? wc_get_cart_url() : home_url('/cart-2/'),
            'checkoutUrl' => function_exists('wc_get_checkout_url') ? wc_get_checkout_url() : home_url('/checkout-2/'),
            'accountUrl' => $client_panel_url,
            'loginUrl' => wp_login_url($account_url),
            'portalUrl' => $client_panel_url,
            'logoutUrl' => wp_logout_url(home_url('/')),
            'loggedIn' => is_user_logged_in() ? 1 : 0,
            'countries' => $this->country_names(),
            'billing' => $this->checkout_map_from_form(),
        ));
        wp_add_inline_script('cfs-frontend', $this->js());
        wp_add_inline_script('cfs-frontend', $this->account_menu_js());
        wp_add_inline_script('cfs-frontend', $this->checkout_prefill_js());
    }

    public function process_form() {
        if (!isset($_POST['cfs_form_nonce']) || !wp_verify_nonce(sanitize_text_field(wp_unslash($_POST['cfs_form_nonce'])), 'cfs_form_action')) {
            wp_send_json_error(array('message' => 'Security check failed'));
        }
        if (!function_exists('WC')) {
            wp_send_json_error(array('message' => 'WooCommerce is required'));
        }

        $product_id = $this->allowed_product_id($_POST['cfs_product_id'] ?? 0);
        if (!$product_id) {
            wp_send_json_error(array('message' => 'Invalid company formation package'));
        }

        $uploaded = array();
        foreach (array('photo_id', 'address_proof') as $field) {
            if (!empty($_FILES[$field]['name'][0])) {
                $uploaded[$field] = $this->handle_uploads($field);
            }
        }
        $previous = $this->get_saved_form();
        foreach (array('photo_id', 'address_proof') as $field) {
            if (empty($uploaded[$field]) && !empty($previous['uploaded_files'][$field])) {
                $uploaded[$field] = $previous['uploaded_files'][$field];
            }
        }
        if (empty($uploaded['photo_id']) || empty($uploaded['address_proof'])) {
            wp_send_json_error(array('message' => 'Please upload Photo ID and Proof of Address'));
        }

        $form_data = $this->collect_posted_fields($product_id);
        $form_data['uploaded_files'] = $uploaded;
        $company_name = $form_data['company_name'];
        $sic_code = $form_data['sic_code'];
        if ($company_name === '' || $sic_code === '') {
            wp_send_json_error(array('message' => 'Company name and SIC code are required'));
        }

        if (!WC()->cart) {
            wc_load_cart();
        }

        foreach (WC()->cart->get_cart() as $key => $item) {
            if (in_array((int) $item['product_id'], self::KYC_PRODUCT_IDS, true)) {
                WC()->cart->remove_cart_item($key);
            }
        }
        if (WC()->session) {
            WC()->session->set('ivs_form_data', null);
        }

        $cart_extras = array(
            'company_name' => $company_name,
            'brixen_service' => 'company_formation',
            'unique_key' => md5($product_id . $company_name),
            'cfs_form' => $form_data,
        );

        $found = false;
        foreach (WC()->cart->get_cart() as $key => $item) {
            if ((int) $item['product_id'] === $product_id) {
                WC()->cart->cart_contents[$key]['company_name'] = $company_name;
                WC()->cart->cart_contents[$key]['brixen_service'] = 'company_formation';
                WC()->cart->cart_contents[$key]['cfs_form'] = $form_data;
                $found = true;
                break;
            }
        }
        if ($found) {
            WC()->cart->set_session();
        } else {
            $added = WC()->cart->add_to_cart($product_id, 1, 0, array(), $cart_extras);
            if (!$added) {
                wp_send_json_error(array('message' => 'Could not add the company formation package to the cart'));
            }
        }

        $this->store_draft($form_data);
        WC()->session->set(self::SESSION_KEY, $form_data);

        $intent = sanitize_text_field(wp_unslash($_POST['cfs_intent'] ?? 'checkout'));
        if ($intent === 'cart') {
            wp_send_json_success(array(
                'added' => true,
                'message' => 'Saved to your cart. You can refresh this page and your details will still be here.',
                'cart_url' => wc_get_cart_url(),
                'checkout_url' => wc_get_checkout_url(),
                'cart_count' => WC()->cart->get_cart_contents_count(),
            ));
        }

        wp_send_json_success(array(
            'redirect' => wc_get_checkout_url(),
        ));
    }

    private function handle_uploads($field_name) {
        if (!function_exists('wp_handle_upload')) {
            require_once ABSPATH . 'wp-admin/includes/file.php';
        }
        $files = $_FILES[$field_name];
        $uploaded = array();
        $allowed = array('image/jpeg', 'image/png', 'application/pdf');
        foreach ($files['name'] as $i => $name) {
            if (!$name) {
                continue;
            }
            if (count($uploaded) >= 5) {
                break;
            }
            if ((int) $files['size'][$i] > 3 * 1024 * 1024 * 1024) {
                continue;
            }
            $file = array(
                'name' => $files['name'][$i],
                'type' => $files['type'][$i],
                'tmp_name' => $files['tmp_name'][$i],
                'error' => $files['error'][$i],
                'size' => $files['size'][$i],
            );
            $check = wp_check_filetype_and_ext($file['tmp_name'], $file['name']);
            $mime = $check['type'] ?: $file['type'];
            if (!in_array($mime, $allowed, true)) {
                continue;
            }
            $moved = wp_handle_upload($file, array('test_form' => false));
            if ($moved && empty($moved['error'])) {
                $uploaded[] = array(
                    'path' => $moved['file'],
                    'url' => $moved['url'],
                    'name' => $file['name'],
                );
            }
        }
        return $uploaded;
    }

    public function cart_item_service_label($item_data, $cart_item) {
        if (!empty($cart_item['brixen_service']) && $cart_item['brixen_service'] === 'company_formation') {
            $item_data[] = array(
                'key' => 'Service',
                'value' => 'Company Formation',
                'display' => 'Company Formation',
            );
        }
        if (!empty($cart_item['company_name'])) {
            $item_data[] = array(
                'key' => 'Company',
                'value' => $cart_item['company_name'],
                'display' => $cart_item['company_name'],
            );
        }
        if (!empty($cart_item['cfs_form']['director_name'])) {
            $item_data[] = array(
                'key' => 'Director',
                'value' => $cart_item['cfs_form']['director_name'],
                'display' => $cart_item['cfs_form']['director_name'],
            );
        }
        return $item_data;
    }

    public function save_line_item_service($item, $cart_item_key, $values, $order) {
        if (!empty($values['brixen_service']) && $values['brixen_service'] === 'company_formation') {
            $item->add_meta_data('Service', 'Company Formation', true);
        }
        if (!empty($values['company_name'])) {
            $item->add_meta_data('Company Name', $values['company_name'], true);
        }
    }

    public function save_form_data_to_order($order_id) {
        if (!function_exists('WC') || !WC()->session) {
            return;
        }
        $form_data = WC()->session->get(self::SESSION_KEY);
        if (!is_array($form_data)) {
            $form_data = array();
        }
        if (empty($form_data['director_name']) && WC()->cart) {
            foreach (WC()->cart->get_cart() as $item) {
                if (!empty($item['cfs_form']) && is_array($item['cfs_form'])) {
                    $form_data = $item['cfs_form'];
                    break;
                }
            }
        }
        if (empty($form_data['director_name'])) {
            return;
        }
        $order = wc_get_order($order_id);
        if (!$order || !$this->order_has_formation_product($order) || $this->order_has_kyc_product($order)) {
            return;
        }
        foreach ($form_data as $key => $value) {
            if ($key === 'uploaded_files' && is_array($value)) {
                foreach ($value as $file_key => $files) {
                    if (!is_array($files)) {
                        continue;
                    }
                    foreach ($files as $index => $file) {
                        if (!empty($file['url'])) {
                            $meta = '_cfs_' . $file_key . '_' . ($index + 1);
                            $order->update_meta_data($meta . '_url', $file['url']);
                            $order->update_meta_data($meta . '_name', $file['name']);
                        }
                    }
                }
            } elseif ($key !== 'timestamp' && $value !== '' && $value !== null) {
                $order->update_meta_data('_cfs_' . $key, $value);
            }
        }
        $order->update_meta_data('_cfs_form_data', $form_data);
        $order->update_meta_data('_cfs_order_type', 'company_formation');
        $order->update_meta_data('_brixen_order_kind', 'company_formation');
        $note = "Company Formation Details\n";
        $note .= 'Package: ' . $form_data['package_name'] . "\n";
        $note .= 'Desired company name: ' . $form_data['company_name'] . "\n";
        $note .= 'SIC: ' . $form_data['sic_code'] . ' — ' . $form_data['business_description'] . "\n";
        $note .= 'Director: ' . $form_data['director_name'] . "\n";
        $note .= 'DOB: ' . $form_data['date_of_birth'] . "\n";
        $note .= 'Passport/CNIC: ' . $form_data['passport_cnic'] . "\n";
        $note .= 'Email: ' . $form_data['registered_email'] . "\n";
        $note .= 'Phone: ' . $form_data['uk_contact_number'] . "\n";
        $note .= 'Role: ' . $form_data['company_role'] . "\n";
        $order->add_order_note($note);
        $order->save();
    }

    public function display_form_data_admin($order) {
        $company = $order->get_meta('_cfs_company_name');
        $director = $order->get_meta('_cfs_director_name');
        if (!$company && !$director) {
            return;
        }
        echo '<div class="order_data_column" style="width:100%">';
        echo '<h3>Company Formation &amp; Director Details</h3>';
        $this->admin_p('Order type', 'Company formation — package product (not Identity Verification / KYC)');
        $this->admin_p('Package', $order->get_meta('_cfs_package_name'));
        $this->admin_p('Desired company name', $company);
        $this->admin_p('SIC code', $order->get_meta('_cfs_sic_code'));
        $this->admin_p('Business activity', $order->get_meta('_cfs_business_description'));
        $this->admin_p('Director', $director);
        $this->admin_p('Date of birth', $order->get_meta('_cfs_date_of_birth'));
        $this->admin_p('Passport/CNIC', $order->get_meta('_cfs_passport_cnic'));
        $this->admin_p('Issuance country', $order->get_meta('_cfs_issuance_country'));
        $this->admin_p('Email', $order->get_meta('_cfs_registered_email'));
        $this->admin_p('UK contact number', $order->get_meta('_cfs_uk_contact_number'));
        $this->admin_p('Role', $order->get_meta('_cfs_company_role'));
        $address = trim(implode(', ', array_filter(array(
            $order->get_meta('_cfs_address_street'),
            $order->get_meta('_cfs_address_line2'),
            $order->get_meta('_cfs_address_city'),
            $order->get_meta('_cfs_address_state'),
            $order->get_meta('_cfs_address_zip'),
            $order->get_meta('_cfs_address_country'),
        ))));
        $this->admin_p('Director home address', $address);
        $registered_address = trim(implode(', ', array_filter(array(
            $order->get_meta('_cfs_registered_address_street'),
            $order->get_meta('_cfs_registered_address_line2'),
            $order->get_meta('_cfs_registered_address_city'),
            $order->get_meta('_cfs_registered_address_state'),
            $order->get_meta('_cfs_registered_address_zip'),
            $order->get_meta('_cfs_registered_address_country'),
        ))));
        $this->admin_p('Registered company address', $registered_address);
        echo '<h4>Uploaded documents</h4>';
        foreach (array('photo_id' => 'Photo ID', 'address_proof' => 'Proof of address') as $key => $label) {
            for ($i = 1; $i <= 5; $i++) {
                $url = $order->get_meta('_cfs_' . $key . '_' . $i . '_url');
                $name = $order->get_meta('_cfs_' . $key . '_' . $i . '_name');
                if ($url) {
                    echo '<p><strong>' . esc_html($label) . ' ' . $i . ':</strong> <a href="' . esc_url($url) . '" target="_blank">' . esc_html($name ?: 'View file') . '</a></p>';
                }
            }
        }
        echo '</div>';
    }

    private function admin_p($label, $value) {
        if ($value === '' || $value === null) {
            return;
        }
        echo '<p><strong>' . esc_html($label) . ':</strong> ' . esc_html($value) . '</p>';
    }

    public function add_to_order_emails($order, $sent_to_admin, $plain_text, $email) {
        $company = $order->get_meta('_cfs_company_name');
        if (!$company) {
            return;
        }
        echo $plain_text
            ? "\nCompany formation: {$company}\nSIC: " . $order->get_meta('_cfs_sic_code') . "\n"
            : '<p><strong>Company formation:</strong> ' . esc_html($company) . '<br><strong>SIC:</strong> ' . esc_html($order->get_meta('_cfs_sic_code')) . '</p>';
    }

    public function print_checkout_button_css() {
        if (!function_exists('is_checkout') || !is_checkout()) {
            return;
        }
        echo '<style id="brixen-checkout-step-buttons">' . $this->checkout_button_css() . '</style>';
    }

    private function checkout_button_css() {
        return <<<'CSS'
#billing_state_field,
#shipping_state_field,
.woocommerce-checkout #billing_state_field,
.woocommerce-checkout #shipping_state_field,
.ea-woo-checkout #billing_state_field,
.ea-woo-checkout #shipping_state_field {
  display: none !important;
}
.woocommerce-checkout .steps-buttons,
.ea-woo-checkout .steps-buttons {
  display: flex !important;
  align-items: center !important;
  justify-content: flex-start !important;
  gap: 16px !important;
  margin-top: 20px !important;
  flex-wrap: wrap !important;
}
.woocommerce-checkout button.ea-woo-checkout-btn-prev,
.woocommerce-checkout button.ea-woo-checkout-btn-next,
.woocommerce-checkout #ea_place_order,
.woocommerce-checkout #place_order,
.ea-woo-checkout button.ea-woo-checkout-btn-prev,
.ea-woo-checkout button.ea-woo-checkout-btn-next,
.ea-woo-checkout #ea_place_order,
.ea-woo-checkout #place_order {
  margin: 0 !important;
  height: 48px !important;
  min-height: 48px !important;
  max-height: 48px !important;
  padding: 0 36px !important;
  line-height: 48px !important;
  border: 0 !important;
  border-radius: 6px !important;
  font-size: 16px !important;
  font-weight: 700 !important;
  align-items: center !important;
  justify-content: center !important;
  box-sizing: border-box !important;
  vertical-align: middle !important;
  float: none !important;
  position: static !important;
  top: auto !important;
  transform: none !important;
}
.woocommerce-checkout button.ea-woo-checkout-btn-prev,
.woocommerce-checkout button.ea-woo-checkout-btn-next,
.ea-woo-checkout button.ea-woo-checkout-btn-prev,
.ea-woo-checkout button.ea-woo-checkout-btn-next {
  display: inline-flex !important;
}
.woocommerce-checkout button.ea-woo-checkout-btn-prev,
.ea-woo-checkout button.ea-woo-checkout-btn-prev,
.woocommerce-checkout .steps-buttons button.ea-woo-checkout-btn-prev {
  background-image: linear-gradient(107deg, #008075 20%, #00a394 100%) !important;
  background-color: #008075 !important;
  color: #fff !important;
}
.woocommerce-checkout button.ea-woo-checkout-btn-next,
.ea-woo-checkout button.ea-woo-checkout-btn-next,
.woocommerce-checkout #ea_place_order,
.woocommerce-checkout #place_order,
.ea-woo-checkout #ea_place_order,
.ea-woo-checkout #place_order {
  background-image: linear-gradient(107deg, #003971 20%, #008075 100%) !important;
  background-color: #003971 !important;
  color: #fff !important;
}
.woocommerce-checkout .form-row input.input-text,
.woocommerce-checkout .form-row textarea,
.woocommerce-checkout .form-row select,
.ea-woo-checkout .form-row input.input-text,
.ea-woo-checkout .form-row select {
  height: 48px !important;
  min-height: 48px !important;
  border: 1px solid #d2d2d7 !important;
  border-radius: 12px !important;
  padding: 0 16px !important;
  font-size: 16px !important;
  background: #fbfbfd !important;
  box-shadow: none !important;
}
.woocommerce-checkout .form-row textarea {
  height: auto !important;
  padding: 12px 16px !important;
}
.woocommerce-checkout .form-row input.input-text:focus,
.woocommerce-checkout .form-row select:focus {
  border-color: #003971 !important;
  background: #fff !important;
  box-shadow: 0 0 0 4px rgba(0,57,113,.12) !important;
}
.woocommerce-checkout .form-row label,
.ea-woo-checkout .form-row label {
  font-size: 13px !important;
  font-weight: 600 !important;
  color: #6e6e73 !important;
  margin-bottom: 8px !important;
}
.woocommerce-checkout .select2-container .select2-selection--single {
  height: 48px !important;
  border: 1px solid #d2d2d7 !important;
  border-radius: 12px !important;
  background: #fbfbfd !important;
}
.woocommerce-checkout .select2-container--default .select2-selection--single .select2-selection__rendered {
  line-height: 46px !important;
  padding-left: 16px !important;
  font-size: 16px !important;
  color: #1d1d1f !important;
}
.woocommerce-checkout .select2-container--default .select2-selection--single .select2-selection__arrow {
  height: 46px !important;
}
@media screen and (min-width: 799px) {
  button.ea-woo-checkout-btn-next {
    margin-top: 0 !important;
    margin-left: 0 !important;
  }
}
CSS;
    }

    private function css() {
        return <<<'CSS'
.cfs-form-container{max-width:820px;margin:24px auto 64px;padding:40px 36px 48px;background:#fff;border-radius:20px;box-shadow:0 1px 2px rgba(0,0,0,.04),0 12px 32px rgba(0,0,0,.06);font-family:-apple-system,BlinkMacSystemFont,"SF Pro Text",Inter,system-ui,sans-serif;box-sizing:border-box}
.cfs-banner{margin:0 0 28px;padding:16px 18px;background:#f5f5f7;border:0;border-radius:14px;color:#1d1d1f}
.cfs-banner strong{display:block;font-size:17px;margin-bottom:4px;font-weight:600}
.cfs-banner span{font-size:13px;line-height:1.45;color:#6e6e73}
.cfs-form-section{margin:0 0 8px;padding:0 0 28px;background:transparent;border:0;border-bottom:1px solid #e8e8ed;border-radius:0}
.cfs-form-section:last-of-type{border-bottom:0;padding-bottom:8px}
.cfs-heading{color:#1d1d1f;font-size:22px;font-weight:600;letter-spacing:-.02em;margin:0 0 20px}
.cfs-question{margin:18px 0 10px;font-size:16px;font-weight:600;color:#1d1d1f}
.cfs-form-row{display:flex;flex-wrap:wrap;gap:16px;margin-bottom:16px}
.cfs-form-col{flex:1;min-width:240px}
.cfs-label{display:block;margin-bottom:8px;font-weight:600;font-size:13px;color:#6e6e73}
.cfs-required{color:#c00}
.cfs-input,.cfs-file-input,.cfs-select,textarea.cfs-input{width:100%;height:48px;padding:0 16px;border:1px solid #d2d2d7;border-radius:12px;font-size:17px;box-sizing:border-box;background:#fbfbfd;color:#1d1d1f;transition:border-color .15s,box-shadow .15s,background .15s}
textarea.cfs-input{height:auto;padding:12px 16px;line-height:1.45}
.cfs-input:focus,.cfs-select:focus,textarea.cfs-input:focus,.cfs-file-input:focus{outline:none;background:#fff;border-color:#003971;box-shadow:0 0 0 4px rgba(0,57,113,.12)}
.cfs-input-locked{background:#f5f5f7;color:#6e6e73;cursor:not-allowed}
.cfs-hint{font-size:13px;color:#6e6e73;margin:8px 0 0;line-height:1.45}
.cfs-radio-group,.cfs-checkbox-group{display:flex;flex-direction:column;gap:10px}
.cfs-radio-label,.cfs-checkbox-label{display:flex;gap:12px;align-items:flex-start;background:#fbfbfd;border:1px solid #d2d2d7;border-radius:12px;padding:14px 16px}
.cfs-radio-label:has(input:checked),.cfs-checkbox-label:has(input:checked){border-color:#003971;background:#f3f7fb}
.cfs-submit-section{text-align:center;margin-top:18px;display:flex;flex-wrap:wrap;gap:12px;justify-content:center;align-items:center}
.cfs-submit-btn,.cfs-add-cart-btn{background:#003971;color:#fff;border:0;padding:0 28px;height:48px;font-size:17px;border-radius:12px;cursor:pointer;font-weight:600}
.cfs-add-cart-btn{background:#008075}
.cfs-submit-btn:hover,.cfs-add-cart-btn:hover{filter:brightness(1.06)}
.cfs-cart-notice{width:100%;margin:8px 0 0;font-weight:600;color:#003971}
.cfs-saved-files{color:#003971;font-weight:600}
.cfs-combobox{position:relative}
.cfs-sic-results,.cfs-country-results{margin-top:6px;border:1px solid #d2d2d7;border-radius:12px;background:#fff;max-height:260px;overflow:auto;z-index:30;position:absolute;left:0;right:0;box-shadow:0 12px 28px rgba(0,0,0,.12)}
.cfs-sic-item{display:block;width:100%;text-align:left;padding:12px 14px;border:0;border-bottom:1px solid #f5f5f7;background:#fff;cursor:pointer;font-size:15px}
.cfs-sic-item:hover{background:#f5f5f7}
.cfs-sic-code{font-weight:700;color:#003971;margin-right:8px}
.cfs-sic-selected{margin:8px 0 0;font-weight:600;color:#003971}
.cfs-postcode-finder{display:flex;gap:10px;align-items:stretch}
.cfs-postcode-finder .cfs-input{flex:1;min-width:0}
.cfs-postcode-btn{background:#003971;color:#fff;border:0;padding:0 18px;border-radius:12px;font-weight:600;cursor:pointer;white-space:nowrap;font-size:15px;height:48px}
.cfs-postcode-btn:disabled{opacity:.7;cursor:wait}
.cfs-address-results{max-height:280px;margin-top:8px;position:relative}
#cfs-reg-postcode-suggest,#cfs-sic-results{position:relative}
@media(max-width:768px){.cfs-form-container{padding:24px 18px;border-radius:16px}.cfs-form-row{flex-direction:column}.cfs-postcode-finder{flex-direction:column}}
CSS;
    }

    private function account_menu_css() {
        return <<<'CSS'
.brixen-account-menu{list-style:none;margin:0;padding:0}
.brixen-account-dropdown{position:fixed;top:0;left:0;min-width:180px;margin:0;padding:6px 0;list-style:none;background:#fff;border:1px solid #e5e5e5;border-radius:6px;box-shadow:0 8px 24px rgba(0,0,0,.12);display:none;z-index:999999}
.brixen-account-dropdown.is-open{display:block}
.brixen-account-dropdown li{margin:0;padding:0;display:block}
.brixen-account-dropdown a{display:flex;align-items:center;gap:10px;padding:10px 16px;color:inherit;text-decoration:none!important;font-weight:600;font-size:15px;white-space:nowrap;line-height:1.2}
.brixen-account-dropdown a:hover{background:#f5f5f5}
.brixen-account-dropdown i{width:16px;text-align:center;color:inherit}
CSS;
    }

    private function account_menu_js() {
        return <<<'JS'
jQuery(function($){
    var cfg = window.cfsAjax || {};
    var cartUrl = cfg.cartUrl || '/cart-2/';
    var loginUrl = cfg.loginUrl || cfg.accountUrl || '/client-panel/';
    var portalUrl = cfg.portalUrl || cfg.accountUrl || loginUrl;
    var logoutUrl = cfg.logoutUrl || '/wp-login.php?action=logout';
    var loggedIn = parseInt(cfg.loggedIn, 10) === 1;
    var hideTimer = null;

    function place($toggle, $menu){
        var r = $toggle[0].getBoundingClientRect();
        $menu.css({color: getComputedStyle($toggle[0]).color, fontFamily: getComputedStyle($toggle[0]).fontFamily});
        $menu.addClass('is-open');
        var mw = $menu.outerWidth();
        var left = Math.round(r.left);
        if (left + mw > window.innerWidth - 8) left = Math.round(r.right - mw);
        if (left < 8) left = 8;
        $menu.css({top: Math.round(r.bottom + 6) + 'px', left: left + 'px'});
    }

    function hide($toggle, $menu){
        $menu.removeClass('is-open');
        $toggle.attr('aria-expanded', 'false');
    }

    function enhance($a){
        if ($a.data('brixenAccount')) return;
        $a.data('brixenAccount', 1);
        var $li = $a.closest('li');
        if ($li.length) $li.addClass('brixen-account-menu');
        $a.addClass('brixen-account-toggle').attr({'aria-haspopup':'true','aria-expanded':'false'});
        var $menu = $('<ul class="brixen-account-dropdown" role="menu"></ul>');
        $menu.append('<li role="none"><a role="menuitem" href="' + cartUrl + '"><i class="fas fa-shopping-cart" aria-hidden="true"></i> Cart</a></li>');
        if (loggedIn) {
            $menu.append('<li role="none"><a role="menuitem" href="' + portalUrl + '"><i class="fas fa-user-circle" aria-hidden="true"></i> Client Portal</a></li>');
            $menu.append('<li role="none"><a role="menuitem" href="' + logoutUrl + '"><i class="fas fa-sign-out-alt" aria-hidden="true"></i> Logout</a></li>');
        } else {
            $menu.append('<li role="none"><a role="menuitem" href="' + loginUrl + '"><i class="fas fa-sign-in-alt" aria-hidden="true"></i> Log in</a></li>');
        }
        $('body').append($menu);

        function openMenu(){
            clearTimeout(hideTimer);
            place($a, $menu);
            $a.attr('aria-expanded', 'true');
        }
        function scheduleHide(){
            clearTimeout(hideTimer);
            hideTimer = setTimeout(function(){ hide($a, $menu); }, 160);
        }

        $a.add($menu).on('mouseenter focusin', openMenu).on('mouseleave focusout', scheduleHide);
        $a.on('click', function(e){
            if (window.matchMedia('(max-width: 1024px)').matches) {
                e.preventDefault();
                if ($menu.hasClass('is-open')) hide($a, $menu);
                else openMenu();
            }
        });
        $(window).on('scroll resize', function(){
            if ($menu.hasClass('is-open')) place($a, $menu);
        });
        $(document).on('click', function(e){
            if (!$(e.target).closest($a).length && !$(e.target).closest($menu).length) hide($a, $menu);
        });
    }

    $('a[href*="client-panel"], a[href*="my-account"]').each(function(){
        var $a = $(this);
        if (!$a.closest('.ct-link-items').length) return;
        var text = $.trim($a.text().replace(/\s+/g, ' '));
        if (/^My Account$/i.test(text)) enhance($a);
    });
});
JS;
    }

    private function checkout_prefill_js() {
        return <<<'JS'
jQuery(function($){
    var billing = (window.cfsAjax && cfsAjax.billing) ? cfsAjax.billing : {};
    function fillBilling(){
        Object.keys(billing).forEach(function(id){
            var $el = $('#' + id);
            if (!$el.length) $el = $('[name="' + id + '"]');
            if (!$el.length || !billing[id]) return;
            $el.val(billing[id]).trigger('change');
        });
    }
    function searchableCountry(){
        var $fields = $('#billing_country, #shipping_country');
        if (!$fields.length) return;
        if ($.fn.selectWoo) {
            $fields.each(function(){
                var $s = $(this);
                if ($s.hasClass('select2-hidden-accessible')) return;
                $s.selectWoo({width:'100%', placeholder:'Type a country name'});
            });
            return;
        }
        $fields.each(function(){
            var $select = $(this);
            if ($select.data('cfsSearchable') || $select.parent().find('.cfs-country-search').length) return;
            $select.data('cfsSearchable', 1);
            var $wrap = $('<div class="cfs-combobox"></div>');
            var current = $.trim($select.find('option:selected').text());
            var $input = $('<input type="text" class="input-text cfs-country-search" autocomplete="off" placeholder="Type a country name">').val(current);
            $select.hide().before($wrap);
            $wrap.append($input).append($select);
            var $list = $('<div class="cfs-country-results cfs-sic-results" hidden></div>');
            $wrap.append($list);
            var options = $select.find('option').map(function(){
                return {value: this.value, label: $.trim($(this).text())};
            }).get().filter(function(row){ return row.value; });
            function render(q){
                q = (q || '').toLowerCase();
                var matches = options.filter(function(row){ return row.label.toLowerCase().indexOf(q) !== -1; }).slice(0, 12);
                $list.empty();
                matches.forEach(function(row){
                    var $btn = $('<button type="button" class="cfs-sic-item"></button>').text(row.label);
                    $btn.on('mousedown', function(e){
                        e.preventDefault();
                        $select.val(row.value).trigger('change');
                        $input.val(row.label);
                        $list.prop('hidden', true);
                    });
                    $list.append($btn);
                });
                $list.prop('hidden', !matches.length);
            }
            $input.on('focus input', function(){ render($input.val()); });
            $input.on('blur', function(){ setTimeout(function(){ $list.prop('hidden', true); }, 150); });
        });
    }
    fillBilling();
    searchableCountry();
    $(document).on('click', '.ea-woo-checkout-btn-next, .ea-woo-checkout-btn-prev', function(){
        setTimeout(function(){ fillBilling(); searchableCountry(); }, 80);
    });
});
JS;
    }

    private function js() {
        return <<<'JS'
jQuery(function($){
    var $form = $('#cfs-formation-form');
    if (!$form.length) return;

    var countries = cfsAjax.countries || [];
    $form.find('.cfs-combobox').each(function(){
        var $box = $(this);
        var $input = $box.find('.cfs-country-search');
        var $hidden = $box.find('.cfs-country-value');
        var $list = $box.find('.cfs-country-results');
        function pick(name){
            $input.val(name);
            $hidden.val(name);
            $list.empty().prop('hidden', true);
        }
        function render(q){
            q = (q || '').toLowerCase();
            var matches = countries.filter(function(name){
                return name.toLowerCase().indexOf(q) !== -1;
            }).slice(0, 12);
            $list.empty();
            if (!matches.length) {
                $list.append('<div class="cfs-sic-item">No matching country</div>').prop('hidden', false);
                return;
            }
            matches.forEach(function(name){
                var $btn = $('<button type="button" class="cfs-sic-item"></button>').text(name);
                $btn.on('mousedown', function(e){ e.preventDefault(); pick(name); });
                $list.append($btn);
            });
            $list.prop('hidden', false);
        }
        $input.on('focus input', function(){ render($input.val()); });
        $input.on('blur', function(){
            setTimeout(function(){
                var typed = $.trim($input.val());
                var exact = countries.find(function(name){ return name.toLowerCase() === typed.toLowerCase(); });
                if (exact) pick(exact);
                $list.prop('hidden', true);
            }, 150);
        });
    });

    var sicTimer = null;
    $('#cfs-sic-search').on('input', function(){
        var q = $(this).val();
        clearTimeout(sicTimer);
        if (q.length < 2) { $('#cfs-sic-results').empty().prop('hidden', true); return; }
        sicTimer = setTimeout(function(){
            $.get(cfsAjax.ajaxUrl, {action:'cfs_search_sic', nonce:cfsAjax.sicNonce, q:q})
                .done(function(res){
                    var items = (res && res.success && res.data) ? res.data : [];
                    var $box = $('#cfs-sic-results').empty();
                    if (!items.length) {
                        $box.append('<div class="cfs-sic-item">No matching Companies House SIC codes</div>').prop('hidden', false);
                        return;
                    }
                    items.forEach(function(row){
                        var $btn = $('<button type="button" class="cfs-sic-item"></button>');
                        $btn.append($('<span class="cfs-sic-code"></span>').text(row.code));
                        $btn.append(document.createTextNode(row.description));
                        $btn.on('click', function(){
                            $('#cfs-sic-code').val(row.code);
                            $('#cfs-sic-search').val(row.code + ' — ' + row.description);
                            $('#cfs-sic-selected').text('Selected: ' + row.code + ' — ' + row.description);
                            if (!$('#cfs-business-description').val()) {
                                $('#cfs-business-description').val(row.description);
                            }
                            $box.empty().prop('hidden', true);
                        });
                        $box.append($btn);
                    });
                    $box.prop('hidden', false);
                });
        }, 220);
    });

    $(document).on('click', function(e){
        if (!$(e.target).closest('#cfs-sic-search, #cfs-sic-results').length) {
            $('#cfs-sic-results').prop('hidden', true);
        }
        if (!$(e.target).closest('.cfs-combobox').length) {
            $('.cfs-country-results').prop('hidden', true);
        }
    });

    var pcTimer = null;
    var $pcInput = $('#cfs-reg-postcode');
    var $pcBtn = $('#cfs-reg-postcode-btn');
    var $pcSuggest = $('#cfs-reg-postcode-suggest');
    var $pcResults = $('#cfs-reg-address-results');
    var $pcStatus = $('#cfs-reg-postcode-status');

    function applyRegisteredAddress(addr){
        if (!addr) return;
        $('[name=registered_address_street]').val(addr.street || '');
        $('[name=registered_address_line2]').val(addr.line2 || '');
        $('[name=registered_address_city]').val(addr.city || '');
        $('[name=registered_address_state]').val(addr.state || '');
        $pcInput.val(addr.zip || $pcInput.val());
        if (addr.street) {
            $pcStatus.text('Address filled from postcode search. You can still edit the fields if needed.');
            $pcResults.prop('hidden', true);
        } else {
            $pcStatus.text('Postcode found. Enter the building and street address below.');
            $('[name=registered_address_street]').trigger('focus');
        }
    }

    function renderAddressSelect(addresses){
        $pcResults.empty();
        if (!addresses.length) {
            $pcResults.prop('hidden', true);
            return;
        }
        if (addresses.length === 1 && addresses[0].street) {
            applyRegisteredAddress(addresses[0]);
            $pcResults.prop('hidden', true);
            return;
        }
        addresses.forEach(function(addr){
            var $btn = $('<button type="button" class="cfs-sic-item"></button>').text(addr.label || addr.zip);
            $btn.on('click', function(){
                applyRegisteredAddress(addr);
            });
            $pcResults.append($btn);
        });
        $pcResults.prop('hidden', false);
        $pcStatus.text('Select the registered office address from the list (' + addresses.length + ' found).');
        if (addresses[0] && addresses[0].city) {
            $('[name=registered_address_city]').val(addresses[0].city);
            $('[name=registered_address_state]').val(addresses[0].state || '');
            $pcInput.val(addresses[0].zip || $pcInput.val());
        }
    }

    function lookupPostcode(postcode){
        if (!postcode) {
            $pcStatus.text('Enter a UK postcode first, for example SW1A 1AA.');
            $pcInput.trigger('focus');
            return;
        }
        $pcBtn.prop('disabled', true).text('Searching…');
        $pcStatus.text('Searching UK postcode…');
        $pcSuggest.empty().prop('hidden', true);
        $pcResults.empty().prop('hidden', true);
        $.get(cfsAjax.ajaxUrl, {
            action: 'cfs_search_postcode',
            nonce: cfsAjax.postcodeNonce,
            postcode: postcode
        }).done(function(res){
            if (res && res.success && res.data && res.data.addresses && res.data.addresses.length) {
                renderAddressSelect(res.data.addresses);
            } else {
                $pcResults.prop('hidden', true);
                $pcStatus.text((res && res.data && res.data.message) ? res.data.message : 'That UK postcode was not found.');
            }
        }).fail(function(){
            $pcStatus.text('Postcode search failed. Please try again or type the address below.');
        }).always(function(){
            $pcBtn.prop('disabled', false).text('Find Address');
        });
    }

    $pcInput.on('input', function(){
        var q = $(this).val();
        clearTimeout(pcTimer);
        $pcResults.prop('hidden', true);
        if (q.replace(/\s+/g, '').length < 3) {
            $pcSuggest.empty().prop('hidden', true);
            return;
        }
        pcTimer = setTimeout(function(){
            $.get(cfsAjax.ajaxUrl, {
                action: 'cfs_autocomplete_postcode',
                nonce: cfsAjax.postcodeNonce,
                q: q
            }).done(function(res){
                var items = (res && res.success && res.data) ? res.data : [];
                $pcSuggest.empty();
                if (!items.length) {
                    $pcSuggest.prop('hidden', true);
                    return;
                }
                items.forEach(function(pc){
                    var $btn = $('<button type="button" class="cfs-sic-item"></button>').text(pc);
                    $btn.on('click', function(){
                        $pcInput.val(pc);
                        $pcSuggest.empty().prop('hidden', true);
                        lookupPostcode(pc);
                    });
                    $pcSuggest.append($btn);
                });
                $pcSuggest.prop('hidden', false);
            });
        }, 220);
    });

    $pcInput.on('keydown', function(e){
        if (e.key === 'Enter') {
            e.preventDefault();
            lookupPostcode($pcInput.val());
        }
    });

    $pcBtn.on('click', function(){
        lookupPostcode($pcInput.val());
    });

    $form.on('submit', function(e){
        e.preventDefault();
        submitFormation('checkout');
        return false;
    });

    $('#cfs-add-to-cart').on('click', function(){
        submitFormation('cart');
    });

    function resetButtons(){
        $('#cfs-loading').hide();
        $('#cfs-submit-form').prop('disabled', false).text('Continue to checkout');
        $('#cfs-add-to-cart').prop('disabled', false).text('Add to cart');
    }

    function submitFormation(intent){
        if (!$('#cfs-sic-code').val()) {
            alert('Please search and select a company activity SIC code.');
            $('#cfs-sic-search').focus();
            return;
        }
        $('#cfs-loading').show();
        $('#cfs-submit-form, #cfs-add-to-cart').prop('disabled', true);
        $('#cfs-submit-form').text('Processing…');
        if (intent === 'cart') {
            $('#cfs-add-to-cart').text('Adding…');
        }
        var data = new FormData($form.get(0));
        data.append('action', 'cfs_process_form');
        data.append('cfs_intent', intent);
        $.ajax({
            url: cfsAjax.ajaxUrl,
            type: 'POST',
            data: data,
            processData: false,
            contentType: false
        }).done(function(res){
            if (intent === 'cart' && res && res.success && res.data && res.data.added) {
                var cartUrl = (res.data.cart_url || cfsAjax.cartUrl || '/cart/');
                var checkoutUrl = (res.data.checkout_url || cfsAjax.checkoutUrl || '');
                $('#cfs-cart-notice').html(
                    (res.data.message || 'Saved to your cart.') +
                    ' <a href="' + cartUrl + '">View cart</a>' +
                    (checkoutUrl ? ' · <a href="' + checkoutUrl + '">Checkout</a>' : '')
                ).prop('hidden', false);
                persistLocalDraft();
                resetButtons();
                $(document.body).trigger('added_to_cart');
                return;
            }
            if (res && res.success && res.data && res.data.redirect) {
                persistLocalDraft();
                window.location.href = res.data.redirect;
            } else {
                alert((res && res.data && res.data.message) ? res.data.message : 'Could not continue.');
                resetButtons();
            }
        }).fail(function(){
            alert('An error occurred. Please try again.');
            resetButtons();
        });
    }

    var draftTimer = null;
    function persistLocalDraft(){
        try {
            var obj = {};
            $form.find('input, select, textarea').each(function(){
                var $el = $(this);
                var name = $el.attr('name');
                if (!name || $el.attr('type') === 'file' || name === 'cfs_form_nonce') return;
                if ($el.attr('type') === 'radio' && !$el.prop('checked')) return;
                if ($el.attr('type') === 'checkbox') {
                    obj[name] = $el.prop('checked') ? $el.val() : '';
                    return;
                }
                obj[name] = $el.val();
            });
            localStorage.setItem('cfs_draft_' + ($('[name=cfs_product_id]').val() || 'form'), JSON.stringify(obj));
        } catch (err) {}
    }
    function restoreLocalDraft(){
        if ($('[name=director_name]').val()) return;
        try {
            var raw = localStorage.getItem('cfs_draft_' + ($('[name=cfs_product_id]').val() || 'form'));
            if (!raw) return;
            var obj = JSON.parse(raw);
            Object.keys(obj).forEach(function(name){
                var $el = $form.find('[name="' + name + '"]');
                if (!$el.length || $el.attr('type') === 'file' || $el.is('[readonly]')) return;
                if ($el.attr('type') === 'radio') {
                    $el.filter('[value="' + obj[name] + '"]').prop('checked', true);
                    return;
                }
                if ($el.attr('type') === 'checkbox') {
                    $el.prop('checked', obj[name] === '1' || obj[name] === $el.val());
                    return;
                }
                if (!$el.val()) $el.val(obj[name]);
            });
            if (obj.sic_code) {
                $('#cfs-sic-code').val(obj.sic_code);
                if (obj.business_description) {
                    $('#cfs-sic-selected').text('Selected: ' + obj.sic_code + ' — ' + obj.business_description);
                }
            }
        } catch (err) {}
    }
    restoreLocalDraft();
    $form.on('input change', function(){
        clearTimeout(draftTimer);
        draftTimer = setTimeout(function(){
            persistLocalDraft();
            $.post(cfsAjax.ajaxUrl, $form.serialize() + '&action=cfs_save_draft');
        }, 900);
    });
});
JS;
    }
}

Brixen_Company_Formation_Form::get_instance();
