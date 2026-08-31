<?php
/**
 * Rebuild File Details pages from Name Search layout:
 * same step header, File Details marked current, formation form under the heading.
 */
if (!defined('ABSPATH')) {
    require_once dirname(__DIR__, 3) . '/wp-load.php';
}

function cfs_walk(&$els, $fn) {
    foreach ($els as &$el) {
        $fn($el);
        if (!empty($el['elements']) && is_array($el['elements'])) {
            cfs_walk($el['elements'], $fn);
        }
    }
    unset($el);
}

function cfs_activate_step_container(&$target, $source) {
    if (!$target || !$source) {
        return;
    }
    $keys = array(
        'background_background',
        'background_color',
        'background_color_b',
        'background_color_stop',
        'background_gradient_angle',
        'background_hover_color',
        'background_hover_color_b',
        'background_hover_color_stop',
        'background_hover_gradient_angle',
    );
    foreach ($keys as $key) {
        if (isset($source['settings'][$key])) {
            $target['settings'][$key] = $source['settings'][$key];
        }
    }
    if (!isset($target['settings']['__globals__']) || !is_array($target['settings']['__globals__'])) {
        $target['settings']['__globals__'] = array();
    }
    if (!empty($source['settings']['__globals__']) && is_array($source['settings']['__globals__'])) {
        foreach ($source['settings']['__globals__'] as $gkey => $gval) {
            $target['settings']['__globals__'][$gkey] = $gval;
        }
    }
}

$map = array(
    array(
        'source' => 13991,
        'target' => 12897,
        'product_id' => 13990,
        'package_name' => 'Digital Package',
        'heading' => "You have chosen the [highlight text=\"DIGITAL PACKAGE \"] .\n<br>Now file your company details.",
    ),
    array(
        'source' => 14058,
        'target' => 12952,
        'product_id' => 14057,
        'package_name' => 'Professional Package',
        'heading' => "You have chosen the [highlight text=\"PROFESSIONAL PACKAGE \"] .\n<br>Now file your company details.",
    ),
    array(
        'source' => 14238,
        'target' => 12989,
        'product_id' => 14233,
        'package_name' => 'All Inclusive Package',
        'heading' => "You have chosen the [highlight text=\"ALL INCLUSIVE PACKAGE \"] .\n<br>Now file your company details.",
    ),
);

$progress = array(
    'id' => 12770,
    'url' => wp_get_attachment_url(12770),
);

foreach ($map as $row) {
    $raw = get_post_meta($row['source'], '_elementor_data', true);
    $data = json_decode($raw, true);
    if (!is_array($data) || !$data) {
        echo "FAIL source {$row['source']}\n";
        continue;
    }

    $desktop_src = null;
    $mobile_src = null;
    cfs_walk($data, function ($el) use (&$desktop_src, &$mobile_src) {
        if (($el['id'] ?? '') === 'f4f13f5') {
            $desktop_src = $el;
        }
        if (($el['id'] ?? '') === '2db6880') {
            $mobile_src = $el;
        }
    });

    cfs_walk($data, function (&$el) use ($row, $progress, $desktop_src, $mobile_src) {
        $id = $el['id'] ?? '';
        if ($id === 'd4ff1b6' && $desktop_src) {
            cfs_activate_step_container($el, $desktop_src);
        }
        if ($id === '848a438' && $mobile_src) {
            cfs_activate_step_container($el, $mobile_src);
        }
        if ($id === 'c7ef8da' && !empty($progress['url'])) {
            $el['settings']['image']['id'] = $progress['id'];
            $el['settings']['image']['url'] = $progress['url'];
            $el['settings']['image']['source'] = 'library';
        }
        if ($id === 'd2ed3ed') {
            $el['settings']['title'] = $row['heading'];
        }
        if ($id === '6aa46a0') {
            $el['settings']['shortcode'] = sprintf(
                '[company_formation_form product_id="%d" package_name="%s"]',
                $row['product_id'],
                $row['package_name']
            );
        }
    });

    update_post_meta($row['target'], '_elementor_data_backup_before_file_details', get_post_meta($row['target'], '_elementor_data', true));
    $saved = wp_slash(wp_json_encode($data));
    update_post_meta($row['target'], '_elementor_data', $saved);
    update_post_meta($row['target'], '_elementor_edit_mode', 'builder');
    update_post_meta($row['target'], '_elementor_template_type', 'wp-page');
    echo "OK {$row['target']} {$row['package_name']} bytes=" . strlen($saved) . "\n";
}

if (class_exists('\Elementor\Plugin')) {
    \Elementor\Plugin::$instance->files_manager->clear_cache();
    echo "elementor cache cleared\n";
}
