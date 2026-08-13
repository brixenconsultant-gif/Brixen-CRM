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
            $items = array();
            foreach ($order->get_items() as $item) {
                $items[] = $item->get_name();
            }
            $service_name = !empty($items) ? implode(', ', $items) : 'Corporate Formation Service';

            $order_payloads[] = array(
                'woocommerce_order_id' => (string) $order->get_id(),
                'order_number'         => '#' . $order->get_order_number(),
                'wordpress_user_id'   => (string) $order->get_customer_id(),
                'email'                => $order->get_billing_email(),
                'full_name'            => $order->get_billing_first_name() . ' ' . $order->get_billing_last_name(),
                'service_name'         => $service_name,
                'price'                => (float) $order->get_subtotal(),
                'total'                => (float) $order->get_total(),
                'status'               => ucfirst($order->get_status()),
            );
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
}
