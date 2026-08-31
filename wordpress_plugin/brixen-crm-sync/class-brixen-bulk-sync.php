<?php
/**
 * Brixen CRM Historical Bulk Sync Utility Class
 * Provides batch processing functions for synchronizing existing WordPress Users
 * and WooCommerce Orders with the Brixen CRM.
 *
 * @package BrixenCRMSync
 */

if (!defined('ABSPATH')) {
    exit; // Exit if accessed directly.
}

require_once plugin_dir_path(__FILE__) . 'class-brixen-webhook-sender.php';

class Brixen_CRM_Bulk_Sync {

    /**
     * Synchronize all existing WordPress users/customers to the CRM.
     *
     * @param int $limit Batch limit
     * @return array Results summary
     */
    public static function sync_all_users($limit = 500) {
        $users = get_users(array(
            'number' => $limit,
            'fields' => 'all',
        ));

        $user_payloads = array();

        foreach ($users as $user) {
            $user_payloads[] = array(
                'wordpress_user_id' => (string) $user->ID,
                'email'             => $user->user_email,
                'first_name'        => get_user_meta($user->ID, 'first_name', true),
                'last_name'         => get_user_meta($user->ID, 'last_name', true),
                'full_name'         => $user->display_name ? $user->display_name : $user->user_email,
                'phone'             => get_user_meta($user->ID, 'billing_phone', true),
                'country'           => get_user_meta($user->ID, 'billing_country', true) ?: 'United Kingdom',
            );
        }

        $crm_url = rtrim(Brixen_CRM_Webhook_Sender::get_crm_url(), '/') . '/api/v1/wordpress/sync-users';
        $secret  = Brixen_CRM_Webhook_Sender::get_webhook_secret();

        $payload = array(
            'users' => $user_payloads,
        );

        $json_payload = wp_json_encode($payload);
        $signature    = 'sha256=' . hash_hmac('sha256', $json_payload, $secret);

        $response = wp_remote_post($crm_url, array(
            'headers' => array(
                'Content-Type'       => 'application/json',
                'X-Brixen-Signature' => $signature,
            ),
            'body'      => $json_payload,
            'timeout'   => 30,
            'sslverify' => false,
        ));

        if (is_wp_error($response)) {
            return array('success' => false, 'error' => $response->get_error_message());
        }

        $body = wp_remote_retrieve_body($response);
        return json_decode($body, true);
    }

    /**
     * Synchronize all existing WooCommerce orders to the CRM.
     *
     * @param int $limit Batch limit
     * @return array Results summary
     */
    public static function sync_all_orders($limit = 500) {
        if (!function_exists('wc_get_orders')) {
            return array('success' => false, 'error' => 'WooCommerce is not active');
        }

        $orders = wc_get_orders(array(
            'limit' => $limit,
        ));

        $order_payloads = array();

        foreach ($orders as $order) {
            $order_payloads[] = Brixen_CRM_Sync_Plugin::build_order_payload($order);
        }

        $crm_url = rtrim(Brixen_CRM_Webhook_Sender::get_crm_url(), '/') . '/api/v1/wordpress/sync-orders';
        $secret  = Brixen_CRM_Webhook_Sender::get_webhook_secret();

        $payload = array(
            'orders' => $order_payloads,
        );

        $json_payload = wp_json_encode($payload);
        $signature    = 'sha256=' . hash_hmac('sha256', $json_payload, $secret);

        $response = wp_remote_post($crm_url, array(
            'headers' => array(
                'Content-Type'       => 'application/json',
                'X-Brixen-Signature' => $signature,
            ),
            'body'      => $json_payload,
            'timeout'   => 30,
            'sslverify' => false,
        ));

        if (is_wp_error($response)) {
            return array('success' => false, 'error' => $response->get_error_message());
        }

        $body = wp_remote_retrieve_body($response);
        return json_decode($body, true);
    }

    /**
     * Synchronize WooCommerce products into the CRM services catalog / order filters.
     *
     * @param int $limit Batch limit
     * @return array Results summary
     */
    public static function sync_all_products($limit = 500) {
        if (!function_exists('wc_get_products')) {
            return array('success' => false, 'error' => 'WooCommerce is not active');
        }

        $products = wc_get_products(array(
            'limit'  => $limit,
            'status' => array('publish', 'private', 'draft'),
            'orderby' => 'title',
            'order'   => 'ASC',
            'return'  => 'objects',
        ));

        $product_payloads = array();
        foreach ($products as $product) {
            if (!$product || !is_object($product)) {
                continue;
            }
            $product_id = $product->get_id();
            $cats = array();
            if (function_exists('get_the_terms')) {
                $terms = get_the_terms($product_id, 'product_cat');
                if ($terms && !is_wp_error($terms)) {
                    foreach ($terms as $term) {
                        $cats[] = $term->name;
                    }
                }
            }
            $status = method_exists($product, 'get_status') ? $product->get_status() : 'publish';
            $product_payloads[] = array(
                'woocommerce_product_id' => (string) $product_id,
                'name'                   => $product->get_name(),
                'description'            => wp_strip_all_tags($product->get_short_description() ?: $product->get_description() ?: $product->get_name()),
                'category'               => !empty($cats) ? $cats[0] : 'General',
                'price'                  => (float) $product->get_regular_price() ?: (float) $product->get_price(),
                'status'                 => $status,
                'sku'                    => $product->get_sku(),
            );
        }

        $crm_url = rtrim(Brixen_CRM_Webhook_Sender::get_crm_url(), '/') . '/api/v1/wordpress/sync-products';
        $secret  = Brixen_CRM_Webhook_Sender::get_webhook_secret();

        $payload = array(
            'products' => $product_payloads,
        );

        $json_payload = wp_json_encode($payload);
        $signature    = 'sha256=' . hash_hmac('sha256', $json_payload, $secret);

        $response = wp_remote_post($crm_url, array(
            'headers' => array(
                'Content-Type'       => 'application/json',
                'X-Brixen-Signature' => $signature,
            ),
            'body'      => $json_payload,
            'timeout'   => 60,
            'sslverify' => false,
        ));

        if (is_wp_error($response)) {
            return array('success' => false, 'error' => $response->get_error_message());
        }

        $body = wp_remote_retrieve_body($response);
        return json_decode($body, true);
    }
}
