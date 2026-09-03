<?php
/**
 * One-time update: round prices ending in 9 up to whole pounds.
 *
 * Examples: 159.99 -> 160, 299.99 -> 300, 149.00 -> 150, 29.89 -> 30
 *
 * Run from WordPress root:
 *   wp eval-file wp-content/plugins/brixen-company-formation/update-package-prices.php
 */
if (!defined('ABSPATH')) {
    $wp_load = dirname(__DIR__, 3) . '/wp-load.php';
    if (!file_exists($wp_load)) {
        fwrite(STDERR, "Could not find wp-load.php\n");
        exit(1);
    }
    require_once $wp_load;
}

if (!function_exists('brixen_normalize_retail_price')) {
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
}

function brixen_replace_price_strings($text) {
    if (!is_string($text) || $text === '') {
        return $text;
    }
    return preg_replace_callback('/£([\d,]+(?:\.\d{1,2})?)/', function ($matches) {
        $raw = str_replace(',', '', $matches[1]);
        $normalized = brixen_normalize_retail_price((float) $raw);
        if (abs($normalized - round($normalized)) < 0.001) {
            return '£' . (int) round($normalized);
        }
        return '£' . number_format($normalized, 2, '.', '');
    }, $text);
}

$product_ids = array(13990, 14057, 14233);

echo "=== Brixen price update (ends in 9 -> whole pound) ===\n";

foreach ($product_ids as $product_id) {
    $product = function_exists('wc_get_product') ? wc_get_product($product_id) : null;
    if (!$product) {
        echo "SKIP product {$product_id}: not found\n";
        continue;
    }
    $old = (float) $product->get_regular_price();
    $new = brixen_normalize_retail_price($old);
    if ($new === $old) {
        echo "SKIP product {$product_id}: {$old} (no change)\n";
        continue;
    }
    $product->set_regular_price((string) $new);
    $product->set_price((string) $new);
    $product->save();
    echo "OK product {$product_id}: {$old} -> {$new}\n";
}

$elementor_ids = array(13042, 13077, 13514, 12897, 12952, 12989, 13991, 14058, 14238);
$meta_keys = array('_elementor_data', '_elementor_element_cache');

foreach ($elementor_ids as $post_id) {
    $changed = false;
    foreach ($meta_keys as $meta_key) {
        $raw = get_post_meta($post_id, $meta_key, true);
        if (!is_string($raw) || $raw === '') {
            continue;
        }
        $updated = brixen_replace_price_strings($raw);
        if ($updated === $raw) {
            continue;
        }
        update_post_meta($post_id, $meta_key . '_backup_before_price_round', $raw);
        update_post_meta($post_id, $meta_key, wp_slash($updated));
        echo "OK elementor {$post_id} meta {$meta_key}\n";
        $changed = true;
    }
    if (!$changed) {
        echo "SKIP elementor {$post_id}: no ending-9 prices found\n";
    }
}

if (class_exists('\Elementor\Plugin')) {
    \Elementor\Plugin::$instance->files_manager->clear_cache();
    echo "Elementor cache cleared\n";
}

echo "Done.\n";
