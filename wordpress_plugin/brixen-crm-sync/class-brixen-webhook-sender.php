<?php
/**
 * Brixen CRM Webhook Sender Class
 * Handles HMAC-SHA256 signing, payload building, and HTTP POST communication.
 *
 * @package BrixenCRMSync
 */

if (!defined('ABSPATH')) {
    exit; // Exit if accessed directly.
}

class Brixen_CRM_Webhook_Sender {

    /**
     * Get CRM base URL from WP options or env.
     */
    public static function get_crm_url() {
        $url = get_option('brixen_crm_url', '');
        if (empty($url)) {
            $url = 'https://portal.brixenconsultants.com';
        }
        return $url;
    }

    /**
     * Get Webhook Secret from WP options or env.
     */
    public static function get_webhook_secret() {
        return get_option('brixen_crm_webhook_secret', '');
    }

    /**
     * Verify a signed CRM pull request for order data.
     */
    public static function verify_order_pull_request($order_id, $timestamp, $signature, $max_age_seconds = 300) {
        $secret = self::get_webhook_secret();
        $order_id = trim((string) $order_id);
        $timestamp = trim((string) $timestamp);
        $signature = trim(str_replace('sha256=', '', (string) $signature));
        if ($secret === '' || $order_id === '' || $timestamp === '' || $signature === '') {
            return false;
        }
        if (!ctype_digit($timestamp)) {
            return false;
        }
        if (abs(time() - (int) $timestamp) > $max_age_seconds) {
            return false;
        }
        $expected = hash_hmac('sha256', $order_id . '|' . $timestamp, $secret);
        return hash_equals($expected, $signature);
    }

    /**
     * Send event payload to CRM webhook endpoint.
     *
     * @param string $event_type E.g., 'user.created', 'order.created'
     * @param array  $data Payload data dictionary
     * @return array Response result with status and code
     */
    public static function send_event($event_type, $data) {
        $crm_base = self::get_crm_url();
        $secret   = self::get_webhook_secret();

        if (empty($crm_base) || empty($secret)) {
            return array('success' => false, 'error' => 'Brixen CRM URL or Webhook Secret is unconfigured.');
        }

        $crm_url = rtrim($crm_base, '/') . '/api/v1/wordpress/webhook';

        $event_id = 'evt_' . time() . '_' . wp_generate_password(8, false);

        $payload = array(
            'event_id'   => $event_id,
            'event_type' => $event_type,
            'timestamp'  => time(),
            'data'       => $data,
        );

        $json_payload = wp_json_encode($payload);
        $signature    = 'sha256=' . hash_hmac('sha256', $json_payload, $secret);

        $response = wp_remote_post($crm_url, array(
            'headers' => array(
                'Content-Type'         => 'application/json',
                'X-Brixen-Signature'   => $signature,
                'X-WP-Signature'       => $signature,
            ),
            'body'      => $json_payload,
            'timeout'   => 15,
            'sslverify' => false, // Set to true in HTTPS production environment
        ));

        if (is_wp_error($response)) {
            error_log('Brixen CRM Webhook Error: ' . $response->get_error_message());
            return array(
                'success' => false,
                'error'   => $response->get_error_message(),
            );
        }

        $code = wp_remote_retrieve_response_code($response);
        $body = wp_remote_retrieve_body($response);

        return array(
            'success' => ($code >= 200 && $code < 300),
            'code'    => $code,
            'response' => json_decode($body, true),
        );
    }
}
