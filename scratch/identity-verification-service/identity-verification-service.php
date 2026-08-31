<?php
/**
 * Plugin Name: Identity Verification Service
 * Plugin URI: https://yourwebsite.com/
 * Description: Custom form for Identity Verification Service with WooCommerce integration
 * Version: 1.2.1
 * Author: Your Name
 * License: GPL v2 or later
 */

// Prevent direct access
if (!defined('ABSPATH')) {
    exit;
}

// ==================== MAIN PLUGIN CLASS ====================
if (!class_exists('IdentityVerificationService')) {
class IdentityVerificationService {
    
    private static $instance = null;
    public $product_id = 790; // CHANGE THIS TO YOUR PRODUCT ID
    
    public static function get_instance() {
        if (null === self::$instance) {
            self::$instance = new self();
        }
        return self::$instance;
    }
    
    private function __construct() {
        // Activation hook
        register_activation_hook(__FILE__, array($this, 'activate_plugin'));
        
        // Initialize plugin
        add_action('plugins_loaded', array($this, 'init_plugin'));
    }
    
    public function activate_plugin() {
        // Set default options
        if (!get_option('ivs_settings')) {
            update_option('ivs_settings', array(
                'product_id' => $this->product_id,
                'primary_color' => '#0073aa',
                'button_color' => '#0073aa',
                'button_hover' => '#005177',
                'font_family' => 'Arial, sans-serif'
            ));
        }
        
        flush_rewrite_rules();
    }
    
    public function init_plugin() {
        // Load settings
        $settings = get_option('ivs_settings', array());
        if (isset($settings['product_id'])) {
            $this->product_id = $settings['product_id'];
        }
        
        // Enqueue scripts and styles
        add_action('wp_enqueue_scripts', array($this, 'enqueue_scripts'));
        add_action('admin_enqueue_scripts', array($this, 'admin_scripts'));
        
        // Add shortcode
        add_shortcode('identity_verification_form', array($this, 'form_shortcode'));
        
        // AJAX handlers
        add_action('wp_ajax_ivs_process_form', array($this, 'process_form'));
        add_action('wp_ajax_nopriv_ivs_process_form', array($this, 'process_form'));
        
        // WooCommerce hooks
        if (class_exists('WooCommerce')) {
            add_action('woocommerce_checkout_update_order_meta', array($this, 'save_form_data_to_order'));
            add_action('woocommerce_admin_order_data_after_order_details', array($this, 'display_form_data_admin'));
            add_action('woocommerce_email_order_meta', array($this, 'add_to_order_emails'), 10, 4);
            
            // My Account hooks
            add_filter('woocommerce_account_menu_items', array($this, 'add_myaccount_tab'));
            add_action('init', array($this, 'add_endpoint'));
            add_action('woocommerce_account_identity-verification_endpoint', array($this, 'tab_content'));
            add_filter('query_vars', array($this, 'add_query_vars'));
        }
        
        // Admin menu
        add_action('admin_menu', array($this, 'add_admin_menu'));
        add_action('admin_init', array($this, 'register_settings'));
        
        // Dynamic CSS
        add_action('wp_head', array($this, 'dynamic_css'));
        
        // Direct checkout redirect handler
        add_action('template_redirect', array($this, 'maybe_redirect_to_checkout'));
    }
    
    // ==================== FORM FUNCTIONS ====================
    
    public function form_shortcode() {
        $prefilled_company_name = '';
        ob_start();
        ?>
        <div class="ivs-form-container" id="ivs-form-wrapper">
        <style type="text/css">
        #ivs-form-wrapper.ivs-form-container {
            max-width: 1140px !important;
            margin: 50px auto 40px auto !important;
            margin-left: auto !important;
            margin-right: auto !important;
            padding: 35px 30px !important;
            background: #ffffff !important;
            border-radius: 12px !important;
            box-shadow: 0 4px 25px rgba(0, 57, 113, 0.12) !important;
            box-sizing: border-box !important;
            clear: both !important;
            float: none !important;
            display: block !important;
            position: relative !important;
            z-index: 100 !important;
            width: 100% !important;
            font-family: Inter, system-ui, Arial, sans-serif !important;
        }
        #ivs-form-wrapper .ivs-form-section {
            margin-bottom: 25px !important;
            padding: 25px !important;
            background: #f8fafc !important;
            border: 1px solid #e2e8f0 !important;
            border-radius: 8px !important;
        }
        #ivs-form-wrapper .ivs-form-row {
            display: flex !important;
            flex-wrap: wrap !important;
            gap: 20px !important;
            margin-bottom: 18px !important;
        }
        #ivs-form-wrapper .ivs-form-col {
            flex: 1 !important;
            min-width: 260px !important;
        }
        #ivs-form-wrapper .ivs-input, 
        #ivs-form-wrapper .ivs-file-input, 
        #ivs-form-wrapper .ivs-select {
            width: 100% !important;
            padding: 12px 14px !important;
            border: 1px solid #cbd5e1 !important;
            border-radius: 6px !important;
            font-size: 15px !important;
            box-sizing: border-box !important;
        }
        @media (max-width: 768px) {
            #ivs-form-wrapper.ivs-form-container {
                padding: 20px 15px !important;
                margin: 30px auto !important;
            }
            #ivs-form-wrapper             .ivs-form-row {
                flex-direction: column !important;
            }
        }
        #ivs-form-wrapper .ivs-second-director-section[hidden] {
            display: none !important;
        }
        </style>
            <form id="ivs-verification-form" method="post" enctype="multipart/form-data">
                <!-- Registration Status Section -->
                <div class="ivs-form-section">
                    <!-- Company Information Section -->
                <div class="ivs-form-section">
                    <h3 class="ivs-heading">Company Information</h3>
                    <div class="ivs-form-row">
                        <div class="ivs-form-col">
                            <label class="ivs-label">Company Name <span class="ivs-required">*</span></label>
                            <input type="text" name="company_name" class="ivs-input" value="<?php echo esc_attr($prefilled_company_name); ?>" placeholder="Here is Desired Company name that client want to register" required>
                        </div>
                        <div class="ivs-form-col">
                            <label class="ivs-label">Company Registration Number (CRN) <span class="ivs-sublabel" style="font-weight:normal; font-size:12px; color:#666;">(If already registered)</span></label>
                            <input type="text" name="company_crn" class="ivs-input" placeholder="Enter CRN (optional)">
                        </div>
                    </div>
                </div>
                
                <h3 class="ivs-heading">Have you registered your company through Brixen Consultants?</h3>
                    
                    <div class="ivs-radio-group">
                        <label class="ivs-radio-label">
                            <input type="radio" name="registration_status" value="registered_elsewhere" required>
                            <span class="ivs-radio-text">No, I registered it myself or through another service provider.</span>
                        </label>
                        <label class="ivs-radio-label">
                            <input type="radio" name="registration_status" value="not_registered" required>
                            <span class="ivs-radio-text">Not registered yet. I would like to register a new company.</span>
                        </label>
                    </div>
                    
                    <div id="crn-field-container" style="display: none;">
                        <div class="ivs-form-row">
                            <div class="ivs-form-col">
                                <label class="ivs-label">Company Registration Number (CRN) <span class="ivs-required">*</span></label>
                                <input type="text" name="company_crn" class="ivs-input" placeholder="Enter your company registration number">
                            </div>
                        </div>
                    </div>
                </div>
                
                <!-- Personal Information Section -->
                <div class="ivs-form-section">
                    
                    
                    <div class="ivs-form-row">
                        <div class="ivs-form-col">
                            <label class="ivs-label">Director Full Name <span class="ivs-required">*</span></label>
                            <input type="text" name="director_name" class="ivs-input" placeholder="Enter director's full name" required>
                        </div>
                        <div class="ivs-form-col">
                            <label class="ivs-label">Date of Birth <span class="ivs-required">*</span></label>
                            <input type="date" name="date_of_birth" class="ivs-input" required>
                        </div>
                    </div>
                    
                    <div class="ivs-form-row">
                        <div class="ivs-form-col">
                            <label class="ivs-label">Passport/CNIC No. <span class="ivs-required">*</span></label>
                            <input type="text" name="passport_cnic" class="ivs-input" placeholder="Enter passport or CNIC number" required>
                        </div>
                        <div class="ivs-form-col">
                            <label class="ivs-label">Country of issuance for your Passport/CNIC <span class="ivs-required">*</span></label>
                            <select name="issuance_country" class="ivs-select" required>
                                <option value="">Select Country</option>
                                <?php echo $this->get_countries_options(); ?>
                            </select>
                        </div>
                    </div>
                    
                    <div class="ivs-form-row">
                        <div class="ivs-form-col">
                            <label class="ivs-label">Registered Email <span class="ivs-required">*</span></label>
                            <input type="email" name="registered_email" class="ivs-input" placeholder="Enter your registered email" required>
                        </div>
                    </div>
                </div>
                
                <!-- Role in Company Section -->
                <div class="ivs-form-section">
                    <h3 class="ivs-heading">Role in Company <span class="ivs-required">*</span></h3>
                    
                    <div class="ivs-radio-group">
                        <label class="ivs-radio-label">
                            <input type="radio" name="company_role" value="director" required>
                            <span class="ivs-radio-text">Director</span>
                        </label>
                        <label class="ivs-radio-label">
                            <input type="radio" name="company_role" value="psc" required>
                            <span class="ivs-radio-text">PSC (Person with Significant control)</span>
                        </label>
                        <label class="ivs-radio-label">
                            <input type="radio" name="company_role" value="both" required>
                            <span class="ivs-radio-text">Director & PSC Both</span>
                        </label>
                    </div>
                </div>

                <!-- Second Director / PSC -->
                <div class="ivs-form-section">
                    <div class="ivs-checkbox-group">
                        <label class="ivs-checkbox-label">
                            <input type="checkbox" name="has_second_director" id="ivs-has-second-director" value="1">
                            <span class="ivs-checkbox-text">This company has more than 1 director or PSC</span>
                        </label>
                    </div>

                    <div id="ivs-second-director-section" class="ivs-second-director-section" hidden>
                        <h3 class="ivs-heading">Second Director / PSC Details</h3>

                        <div class="ivs-form-row">
                            <div class="ivs-form-col">
                                <label class="ivs-label">Director Full Name <span class="ivs-required">*</span></label>
                                <input type="text" name="director2_name" class="ivs-input ivs-second-director-field" placeholder="Enter second director's full name">
                            </div>
                            <div class="ivs-form-col">
                                <label class="ivs-label">Date of Birth <span class="ivs-required">*</span></label>
                                <input type="date" name="director2_date_of_birth" class="ivs-input ivs-second-director-field">
                            </div>
                        </div>

                        <div class="ivs-form-row">
                            <div class="ivs-form-col">
                                <label class="ivs-label">Passport/CNIC No. <span class="ivs-required">*</span></label>
                                <input type="text" name="director2_passport_cnic" class="ivs-input ivs-second-director-field" placeholder="Enter passport or CNIC number">
                            </div>
                            <div class="ivs-form-col">
                                <label class="ivs-label">Country of issuance for Passport/CNIC <span class="ivs-required">*</span></label>
                                <select name="director2_issuance_country" class="ivs-select ivs-second-director-field">
                                    <option value="">Select Country</option>
                                    <?php echo $this->get_countries_options(); ?>
                                </select>
                            </div>
                        </div>

                        <div class="ivs-form-row">
                            <div class="ivs-form-col">
                                <label class="ivs-label">Registered Email <span class="ivs-required">*</span></label>
                                <input type="email" name="director2_registered_email" class="ivs-input ivs-second-director-field" placeholder="Enter second director's email">
                            </div>
                        </div>

                        <h4 class="ivs-subheading">Role in Company <span class="ivs-required">*</span></h4>
                        <div class="ivs-radio-group">
                            <label class="ivs-radio-label">
                                <input type="radio" name="director2_company_role" value="director" class="ivs-second-director-field">
                                <span class="ivs-radio-text">Director</span>
                            </label>
                            <label class="ivs-radio-label">
                                <input type="radio" name="director2_company_role" value="psc" class="ivs-second-director-field">
                                <span class="ivs-radio-text">PSC (Person with Significant control)</span>
                            </label>
                            <label class="ivs-radio-label">
                                <input type="radio" name="director2_company_role" value="both" class="ivs-second-director-field">
                                <span class="ivs-radio-text">Director & PSC Both</span>
                            </label>
                        </div>

                        <h4 class="ivs-subheading" style="margin-top: 20px;">Second Director / PSC Home Address</h4>
                        <div class="ivs-form-row">
                            <div class="ivs-form-col">
                                <label class="ivs-label">Street Address <span class="ivs-required">*</span></label>
                                <input type="text" name="director2_address_street" class="ivs-input ivs-second-director-field" placeholder="Enter street address">
                            </div>
                        </div>
                        <div class="ivs-form-row">
                            <div class="ivs-form-col">
                                <label class="ivs-label">Address Line 2</label>
                                <input type="text" name="director2_address_line2" class="ivs-input" placeholder="Enter apartment, suite, unit, etc. (optional)">
                            </div>
                        </div>
                        <div class="ivs-form-row">
                            <div class="ivs-form-col">
                                <label class="ivs-label">City <span class="ivs-required">*</span></label>
                                <input type="text" name="director2_address_city" class="ivs-input ivs-second-director-field" placeholder="Enter city">
                            </div>
                            <div class="ivs-form-col">
                                <label class="ivs-label">Zip/Postal Code <span class="ivs-required">*</span></label>
                                <input type="text" name="director2_address_zip" class="ivs-input ivs-second-director-field" placeholder="Enter zip or postal code">
                            </div>
                        </div>
                        <div class="ivs-form-row">
                            <div class="ivs-form-col">
                                <label class="ivs-label">Country <span class="ivs-required">*</span></label>
                                <select name="director2_address_country" class="ivs-select ivs-second-director-field">
                                    <option value="">Select Country</option>
                                    <?php echo $this->get_countries_options(); ?>
                                </select>
                            </div>
                        </div>

                        <h4 class="ivs-subheading">Second Director / PSC Documents</h4>
                        <div class="ivs-form-row">
                            <div class="ivs-form-col">
                                <label class="ivs-label">Upload Photo ID <span class="ivs-required">*</span></label>
                                <input type="file" name="director2_photo_id[]" class="ivs-file-input ivs-second-director-file" accept=".jpg,.jpeg,.png,.pdf" multiple>
                                <div class="ivs-file-hint">
                                    <p><strong>Max. file size: 3 GB, Max. files: 5.</strong></p>
                                    <p>Upload any of: Passport, National ID, Driving licence.</p>
                                </div>
                            </div>
                        </div>
                        <div class="ivs-form-row">
                            <div class="ivs-form-col">
                                <label class="ivs-label">Upload Proof of Address <span class="ivs-required">*</span></label>
                                <input type="file" name="director2_address_proof[]" class="ivs-file-input ivs-second-director-file" accept=".jpg,.jpeg,.png,.pdf" multiple>
                                <div class="ivs-file-hint">
                                    <p><strong>Max. file size: 3 GB.</strong></p>
                                    <p>Accepted: Bank statement, tax document, driving licence, national ID with address, or utility bill dated within last 3 months.</p>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
                
                <!-- Address Details Section -->
                <div class="ivs-form-section">
                    <h3 class="ivs-heading">Director Home Address <span class="ivs-required">*</span></h3>
                    
                    <div class="ivs-form-row">
                        <div class="ivs-form-col">
                            <label class="ivs-label">Street Address <span class="ivs-required">*</span></label>
                            <input type="text" name="address_street" class="ivs-input" placeholder="Enter street address" required>
                        </div>
                    </div>
                    
                    <div class="ivs-form-row">
                        <div class="ivs-form-col">
                            <label class="ivs-label">Address Line 2</label>
                            <input type="text" name="address_line2" class="ivs-input" placeholder="Enter apartment, suite, unit, etc. (optional)">
                        </div>
                    </div>
                    
                    <div class="ivs-form-row">
                        <div class="ivs-form-col">
                            <label class="ivs-label">City <span class="ivs-required">*</span></label>
                            <input type="text" name="address_city" class="ivs-input" placeholder="Enter city" required>
                        </div>
                        <div class="ivs-form-col">
                            <label class="ivs-label">Zip/Postal Code <span class="ivs-required">*</span></label>
                            <input type="text" name="address_zip" class="ivs-input" placeholder="Enter zip or postal code" required>
                        </div>
                    </div>
                    
                    <div class="ivs-form-row">
                        <div class="ivs-form-col">
                            <label class="ivs-label">Country <span class="ivs-required">*</span></label>
                            <select name="address_country" class="ivs-select" required>
                                <option value="">Select Country</option>
                                <?php echo $this->get_countries_options(); ?>
                            </select>
                        </div>
                    </div>
                </div>
                
                <!-- Document Upload Section -->
                <div class="ivs-form-section">
                    <h3 class="ivs-heading">Document Upload</h3>
                    
                    <!-- Upload Director Photo ID -->
                    <div class="ivs-form-row">
                        <div class="ivs-form-col">
                            <label class="ivs-label">Upload Director Photo ID <span class="ivs-required">*</span></label>
                            <input type="file" name="photo_id[]" class="ivs-file-input" accept=".jpg,.jpeg,.png,.pdf" multiple required>
                            <div class="ivs-file-hint">
                                <p><strong>Max. file size: 3 GB, Max. files: 5.</strong></p>
                                <p>Upload Any of Them (Passport, National Id, Driving license). Must be scanned or photographed clearly. All 4 corners of the ID must be visible. Holograms and security features must be visible in the photo.</p>
                            </div>
                        </div>
                    </div>
                    
                    <!-- Upload Proof of Address -->
                    <div class="ivs-form-row">
                        <div class="ivs-form-col">
                            <label class="ivs-label">Upload Proof of Address <span class="ivs-required">*</span></label>
                            <input type="file" name="address_proof[]" class="ivs-file-input" accept=".jpg,.jpeg,.png,.pdf" multiple required>
                            <div class="ivs-file-hint">
                                <p><strong>Max. file size: 3 GB.</strong></p>
                                <p>The name must match exactly with the passport. Accepted documents:</p>
                                <p>1. Bank Statement</p>
                                <p>2. Tax Document</p>
                                <p>3. Driving License</p>
                                <p>4. National ID with address</p>
                                <p>5. Utility Bill (water/gas/electricity/landline/broadband only, dated within last 3 months)</p>
                                <p><em>(Mobile phone bills are not accepted)</em></p>
                            </div>
                        </div>
                    </div>
                </div>

                <!-- ACSP Compliance Questions -->
                <div class="ivs-form-section">
                    <h3 class="ivs-heading">ACSP COMPLIANCE QUESTIONS</h3>
                    <hr class="ivs-divider">
                    
                    <!-- Purpose of Verification -->
                    <div class="ivs-form-row">
                        <div class="ivs-form-col">
                            <h4 class="ivs-subheading">Purpose of Verification <span class="ivs-required">*</span></h4>
                            <div class="ivs-radio-group">
                                <label class="ivs-radio-label">
                                    <input type="radio" name="verification_purpose" value="uk_incorporation" required>
                                    <span class="ivs-radio-text">UK Company Incorporation</span>
                                </label>
                                <label class="ivs-radio-label">
                                    <input type="radio" name="verification_purpose" value="director_psc" required>
                                    <span class="ivs-radio-text">Director / PSC Verification</span>
                                </label>
                                <label class="ivs-radio-label">
                                    <input type="radio" name="verification_purpose" value="ongoing_compliance" required>
                                    <span class="ivs-radio-text">Ongoing Compliance (ACSP requirement)</span>
                                </label>
                            </div>
                        </div>
                    </div>
                    
                    <!-- PEP Declaration -->
                    <div class="ivs-form-row">
                        <div class="ivs-form-col">
                            <h4 class="ivs-subheading">PEP Declaration</h4>
                            <hr class="ivs-divider">
                            
                            <h5 class="ivs-question">Are you a Politically Exposed Person (PEP)? <span class="ivs-required">*</span></h5>
                            <div class="ivs-radio-group">
                                <label class="ivs-radio-label">
                                    <input type="radio" name="is_pep" value="yes" required>
                                    <span class="ivs-radio-text">Yes</span>
                                </label>
                                <label class="ivs-radio-label">
                                    <input type="radio" name="is_pep" value="no" required>
                                    <span class="ivs-radio-text">No</span>
                                </label>
                            </div>
                            
                            <h5 class="ivs-question" style="margin-top: 20px;">Are you a family member or close associate of a PEP? <span class="ivs-required">*</span></h5>
                            <div class="ivs-radio-group">
                                <label class="ivs-radio-label">
                                    <input type="radio" name="pep_associate" value="yes" required>
                                    <span class="ivs-radio-text">Yes</span>
                                </label>
                                <label class="ivs-radio-label">
                                    <input type="radio" name="pep_associate" value="no" required>
                                    <span class="ivs-radio-text">No</span>
                                </label>
                            </div>
                        </div>
                    </div>
                    
                    <!-- Sanctions Declaration -->
                    <div class="ivs-form-row">
                        <div class="ivs-form-col">
                            <h4 class="ivs-subheading">Sanctions Declaration</h4>
                            <hr class="ivs-divider">
                            
                            <h5 class="ivs-question">Are you subject to any sanctions, investigations, or legal proceedings? <span class="ivs-required">*</span></h5>
                            <div class="ivs-radio-group">
                                <label class="ivs-radio-label">
                                    <input type="radio" name="subject_to_sanctions" value="yes" required>
                                    <span class="ivs-radio-text">Yes</span>
                                </label>
                                <label class="ivs-radio-label">
                                    <input type="radio" name="subject_to_sanctions" value="no" required>
                                    <span class="ivs-radio-text">No</span>
                                </label>
                            </div>
                            
                            <h5 class="ivs-question" style="margin-top: 20px;">Have you ever been convicted of a financial crime? <span class="ivs-required">*</span></h5>
                            <div class="ivs-radio-group">
                                <label class="ivs-radio-label">
                                    <input type="radio" name="financial_crime" value="yes" required>
                                    <span class="ivs-radio-text">Yes</span>
                                </label>
                                <label class="ivs-radio-label">
                                    <input type="radio" name="financial_crime" value="no" required>
                                    <span class="ivs-radio-text">No</span>
                                </label>
                            </div>
                        </div>
                    </div>
                </div>
                
                <!-- Non-Refund Acknowledgement -->
                <div class="ivs-form-section">
                    <div class="ivs-form-row">
                        <div class="ivs-form-col">
                            <h4 class="ivs-subheading">Non-Refund Acknowledgement <span class="ivs-required">*</span></h4>
                            <div class="ivs-checkbox-group">
                                <label class="ivs-checkbox-label">
                                    <input type="checkbox" name="non_refund_ack" value="1" required>
                                    <span class="ivs-checkbox-text">
                                        I understand that the verification fee covers the cost of identity and address checks. If my verification fails due to invalid, false, altered, or unverifiable documents, or unsuccessful live biometric verification, the fee is not refundable.
                                    </span>
                                </label>
                            </div>
                        </div>
                    </div>
                </div>
                
                <?php wp_nonce_field('ivs_form_action', 'ivs_form_nonce'); ?>
                <input type="hidden" name="ivs_form_submitted" value="1">
                
                <div class="ivs-submit-section">
                    <button type="submit" class="ivs-submit-btn" id="ivs-submit-form">
                        Next
                    </button>
                    <div id="ivs-loading" style="display: none; margin-top: 10px;">
                        <p><strong>Processing...</strong></p>
                    </div>
                </div>
            </form>
        </div>
        <script>
        window.ivs_ajax = <?php echo wp_json_encode(array(
            'ajax_url' => admin_url('admin-ajax.php'),
            'checkout_url' => class_exists('WooCommerce') ? wc_get_checkout_url() : '',
        )); ?>;
        (function ivsWaitForJQuery() {
            if (!window.jQuery) {
                setTimeout(ivsWaitForJQuery, 30);
                return;
            }
            <?php echo $this->get_frontend_js(); ?>
        })();
        </script>
        <?php
        return ob_get_clean();
    }
    
    // ==================== FORM HANDLING ====================

    private function collect_posted_form_data($uploaded_files) {
        $has_second = !empty($_POST['has_second_director']);
        return array(
            'company_name' => sanitize_text_field($_POST['company_name'] ?? ''),
            'registration_status' => sanitize_text_field($_POST['registration_status'] ?? ''),
            'company_crn' => isset($_POST['company_crn']) ? sanitize_text_field($_POST['company_crn']) : '',
            'director_name' => sanitize_text_field($_POST['director_name'] ?? ''),
            'date_of_birth' => sanitize_text_field($_POST['date_of_birth'] ?? ''),
            'passport_cnic' => sanitize_text_field($_POST['passport_cnic'] ?? ''),
            'issuance_country' => sanitize_text_field($_POST['issuance_country'] ?? ''),
            'registered_email' => sanitize_email(wp_unslash($_POST['registered_email'] ?? '')),
            'company_role' => sanitize_text_field($_POST['company_role'] ?? ''),
            'has_second_director' => $has_second ? '1' : '0',
            'director2_name' => $has_second ? sanitize_text_field($_POST['director2_name'] ?? '') : '',
            'director2_date_of_birth' => $has_second ? sanitize_text_field($_POST['director2_date_of_birth'] ?? '') : '',
            'director2_passport_cnic' => $has_second ? sanitize_text_field($_POST['director2_passport_cnic'] ?? '') : '',
            'director2_issuance_country' => $has_second ? sanitize_text_field($_POST['director2_issuance_country'] ?? '') : '',
            'director2_registered_email' => $has_second ? sanitize_email(wp_unslash($_POST['director2_registered_email'] ?? '')) : '',
            'director2_company_role' => $has_second ? sanitize_text_field($_POST['director2_company_role'] ?? '') : '',
            'director2_address_street' => $has_second ? sanitize_text_field($_POST['director2_address_street'] ?? '') : '',
            'director2_address_line2' => $has_second ? sanitize_text_field($_POST['director2_address_line2'] ?? '') : '',
            'director2_address_city' => $has_second ? sanitize_text_field($_POST['director2_address_city'] ?? '') : '',
            'director2_address_zip' => $has_second ? sanitize_text_field($_POST['director2_address_zip'] ?? '') : '',
            'director2_address_country' => $has_second ? sanitize_text_field($_POST['director2_address_country'] ?? '') : '',
            'address_street' => sanitize_text_field($_POST['address_street'] ?? ''),
            'address_line2' => isset($_POST['address_line2']) ? sanitize_text_field($_POST['address_line2']) : '',
            'address_city' => sanitize_text_field($_POST['address_city'] ?? ''),
            'address_zip' => sanitize_text_field($_POST['address_zip'] ?? ''),
            'address_country' => sanitize_text_field($_POST['address_country'] ?? ''),
            'verification_purpose' => sanitize_text_field($_POST['verification_purpose'] ?? ''),
            'is_pep' => sanitize_text_field($_POST['is_pep'] ?? ''),
            'pep_associate' => sanitize_text_field($_POST['pep_associate'] ?? ''),
            'subject_to_sanctions' => sanitize_text_field($_POST['subject_to_sanctions'] ?? ''),
            'financial_crime' => sanitize_text_field($_POST['financial_crime'] ?? ''),
            'non_refund_ack' => isset($_POST['non_refund_ack']) ? '1' : '0',
            'uploaded_files' => $uploaded_files,
            'timestamp' => time(),
        );
    }

    private function validate_posted_form_data($form_data) {
        if ($form_data['has_second_director'] === '1') {
            $required = array(
                'director2_name' => 'Second director full name',
                'director2_date_of_birth' => 'Second director date of birth',
                'director2_passport_cnic' => 'Second director passport/CNIC number',
                'director2_issuance_country' => 'Second director passport/CNIC issuance country',
                'director2_registered_email' => 'Second director email',
                'director2_company_role' => 'Second director role in company',
                'director2_address_street' => 'Second director street address',
                'director2_address_city' => 'Second director city',
                'director2_address_zip' => 'Second director postcode',
                'director2_address_country' => 'Second director country',
            );
            foreach ($required as $key => $label) {
                if (empty($form_data[$key])) {
                    return $label . ' is required when adding a second director or PSC.';
                }
            }
            if (empty($form_data['uploaded_files']['director2_photo_id'])) {
                return 'Second director photo ID is required when adding a second director or PSC.';
            }
            if (empty($form_data['uploaded_files']['director2_address_proof'])) {
                return 'Second director proof of address is required when adding a second director or PSC.';
            }
        }
        return '';
    }
    
    public function process_form() {
        // Verify nonce
        if (!isset($_POST['ivs_form_nonce']) || !wp_verify_nonce($_POST['ivs_form_nonce'], 'ivs_form_action')) {
            wp_die('Security check failed');
        }
        
        $uploaded_files = array();
        $file_fields = array('photo_id', 'address_proof', 'director2_photo_id', 'director2_address_proof');
        
        foreach ($file_fields as $field) {
            if (!empty($_FILES[$field]['name'][0])) {
                $uploaded_files[$field] = $this->handle_multiple_file_upload($field);
            }
        }
        
        $form_data = $this->collect_posted_form_data($uploaded_files);
        $validation_error = $this->validate_posted_form_data($form_data);
        if ($validation_error !== '') {
            wp_send_json_error(array('message' => $validation_error));
        }
        
        // Store in session
        if (class_exists('WooCommerce') && isset(WC()->session)) {
            WC()->session->set('ivs_form_data', $form_data);
            WC()->session->set('cfs_form_data', null);
        }
        
        // Add KYC product only — never a company formation package
        if (class_exists('WooCommerce')) {
            // Clear cart first
            WC()->cart->empty_cart();
            
            // Add Identity Verification product to cart
            $added = WC()->cart->add_to_cart($this->product_id, 1, 0, array(), array(
                'brixen_service' => 'identity_verification',
            ));
            
            if ($added) {
                wp_send_json_success(array(
                    'redirect' => wc_get_checkout_url()
                ));
            } else {
                wp_send_json_error(array(
                    'message' => 'Could not add product to cart'
                ));
            }
        }
        
        wp_die();
    }
    
    private function handle_multiple_file_upload($field_name) {
        if (!function_exists('wp_handle_upload')) {
            require_once(ABSPATH . 'wp-admin/includes/file.php');
        }
        
        $files = $_FILES[$field_name];
        $uploaded_files = array();
        
        foreach ($files['name'] as $key => $value) {
            if ($files['name'][$key]) {
                $file = array(
                    'name' => $files['name'][$key],
                    'type' => $files['type'][$key],
                    'tmp_name' => $files['tmp_name'][$key],
                    'error' => $files['error'][$key],
                    'size' => $files['size'][$key]
                );
                
                // Check file size (3GB limit)
                if ($file['size'] > 3 * 1024 * 1024 * 1024) {
                    continue;
                }
                
                $upload_overrides = array('test_form' => false);
                $movefile = wp_handle_upload($file, $upload_overrides);
                
                if ($movefile && !isset($movefile['error'])) {
                    $uploaded_files[] = array(
                        'path' => $movefile['file'],
                        'url' => $movefile['url'],
                        'name' => $file['name']
                    );
                }
            }
        }
        
        return $uploaded_files;
    }
    
    // Direct form submission handler
    public function maybe_redirect_to_checkout() {
        if (isset($_POST['ivs_form_submitted']) && $_POST['ivs_form_submitted'] == '1') {
            if (!isset($_POST['ivs_form_nonce']) || !wp_verify_nonce($_POST['ivs_form_nonce'], 'ivs_form_action')) {
                return;
            }

            $uploaded_files = array();
            $file_fields = array('photo_id', 'address_proof', 'director2_photo_id', 'director2_address_proof');
            foreach ($file_fields as $field) {
                if (!empty($_FILES[$field]['name'][0])) {
                    $uploaded_files[$field] = $this->handle_multiple_file_upload($field);
                }
            }

            $form_data = $this->collect_posted_form_data($uploaded_files);
            $validation_error = $this->validate_posted_form_data($form_data);
            if ($validation_error !== '') {
                wc_add_notice($validation_error, 'error');
                return;
            }

            if (class_exists('WooCommerce') && isset(WC()->session)) {
                WC()->session->set('ivs_form_data', $form_data);
                WC()->session->set('cfs_form_data', null);
            }

            if (class_exists('WooCommerce')) {
                WC()->cart->empty_cart();
                WC()->cart->add_to_cart($this->product_id, 1, 0, array(), array(
                    'brixen_service' => 'identity_verification',
                ));
                wp_redirect(wc_get_checkout_url());
                exit;
            }
        }
    }
    
    // ==================== WOOCOMMERCE INTEGRATION ====================
    
    public function save_form_data_to_order($order_id) {
        $form_data = null;
        
        // Try to get from session
        if (class_exists('WooCommerce') && isset(WC()->session)) {
            $form_data = WC()->session->get('ivs_form_data');
        }
        
        if ($form_data && !empty($form_data['director_name'])) {
            $order = wc_get_order($order_id);
            if (!$order || !$this->order_has_kyc_product($order)) {
                return;
            }
            
            // Save all form data as order meta
            foreach ($form_data as $key => $value) {
                if ($key === 'uploaded_files') {
                    // Save file URLs for each uploaded file
                    if (is_array($value)) {
                        foreach ($value as $file_key => $file_data_array) {
                            if (is_array($file_data_array)) {
                                foreach ($file_data_array as $index => $file_data) {
                                    if (isset($file_data['url'])) {
                                        $meta_key = '_ivs_' . $file_key . '_' . ($index + 1);
                                        $order->update_meta_data($meta_key . '_url', $file_data['url']);
                                        $order->update_meta_data($meta_key . '_name', $file_data['name']);
                                    }
                                }
                            }
                        }
                    }
                } else if ($key === 'source_of_funds') {
                    // Save as serialized array
                    if (is_array($value) && !empty($value)) {
                        $order->update_meta_data('_ivs_' . $key, maybe_serialize($value));
                    }
                } else if (!empty($value)) {
                    $order->update_meta_data('_ivs_' . $key, $value);
                }
            }
            
            // Prepare order note
            $order_note = "Identity Verification Service Details:\n";
            $order_note .= "Registration Status: " . ucfirst(str_replace('_', ' ', $form_data['registration_status'])) . "\n";
            if ($form_data['company_crn']) {
                $order_note .= "Company CRN: {$form_data['company_crn']}\n";
            }
            $order_note .= "Director: {$form_data['director_name']}\n";
            $order_note .= "DOB: {$form_data['date_of_birth']}\n";
            $order_note .= "Passport/CNIC: {$form_data['passport_cnic']}\n";
            $order_note .= "Issuance Country: {$form_data['issuance_country']}\n";
            $order_note .= "Email: {$form_data['registered_email']}\n";
            $order_note .= "Role: " . ucfirst($form_data['company_role']) . "\n";
            if (!empty($form_data['has_second_director']) && $form_data['has_second_director'] === '1') {
                $order_note .= "--- Second Director / PSC ---\n";
                $order_note .= "Director 2: {$form_data['director2_name']}\n";
                $order_note .= "Director 2 DOB: {$form_data['director2_date_of_birth']}\n";
                $order_note .= "Director 2 Passport/CNIC: {$form_data['director2_passport_cnic']}\n";
                $order_note .= "Director 2 Issuance Country: {$form_data['director2_issuance_country']}\n";
                $order_note .= "Director 2 Email: {$form_data['director2_registered_email']}\n";
                $order_note .= "Director 2 Role: " . ucfirst($form_data['director2_company_role']) . "\n";
                $order_note .= "Director 2 Address: {$form_data['director2_address_street']}, {$form_data['director2_address_city']}, {$form_data['director2_address_country']}\n";
            }
            $order_note .= "Address: {$form_data['address_street']}, {$form_data['address_city']}\n";
            $order_note .= "Country: {$form_data['address_country']}\n";
            $order_note .= "Verification Purpose: " . ucfirst(str_replace('_', ' ', $form_data['verification_purpose'])) . "\n";
            $order_note .= "PEP: " . ucfirst($form_data['is_pep']) . "\n";
            $order_note .= "PEP Associate: " . ucfirst($form_data['pep_associate']) . "\n";
            $order_note .= "Sanctions: " . ucfirst($form_data['subject_to_sanctions']) . "\n";
            $order_note .= "Financial Crime: " . ucfirst($form_data['financial_crime']) . "\n";
            $order_note .= "Non-Refund Ack: " . ($form_data['non_refund_ack'] ? 'Yes' : 'No') . "\n";
            $order_note .= "Documents Uploaded:\n";
            $order_note .= "- Photo ID: " . (isset($form_data['uploaded_files']['photo_id']) ? count($form_data['uploaded_files']['photo_id']) : '0') . " files\n";
            $order_note .= "- Address Proof: " . (isset($form_data['uploaded_files']['address_proof']) ? count($form_data['uploaded_files']['address_proof']) : '0') . " files\n";
            if (!empty($form_data['has_second_director']) && $form_data['has_second_director'] === '1') {
                $order_note .= "- Director 2 Photo ID: " . (isset($form_data['uploaded_files']['director2_photo_id']) ? count($form_data['uploaded_files']['director2_photo_id']) : '0') . " files\n";
                $order_note .= "- Director 2 Address Proof: " . (isset($form_data['uploaded_files']['director2_address_proof']) ? count($form_data['uploaded_files']['director2_address_proof']) : '0') . " files\n";
            }
            
            $order->update_meta_data('_brixen_order_kind', 'identity_verification');
            $order->add_order_note($order_note);
            $order->save();
            
            // Clear session
            if (class_exists('WooCommerce') && isset(WC()->session)) {
                WC()->session->set('ivs_form_data', null);
            }
        }
    }
    
    private function order_has_kyc_product($order) {
        $kyc_ids = array((int) $this->product_id, 15145, 790);
        foreach ($order->get_items() as $item) {
            if (in_array((int) $item->get_product_id(), $kyc_ids, true)) {
                return true;
            }
        }
        return false;
    }

    public function display_form_data_admin($order) {
        if (!$this->order_has_kyc_product($order)) {
            return;
        }
        $director_name = $order->get_meta('_ivs_director_name');
        
        if ($director_name) {
            ?>
            <div class="order_data_column" style="width: 100%;">
                <h3>Identity Verification Service Details</h3>
                
                <!-- Registration Status Section -->
                <div style="margin-bottom: 20px;">
                    <h4 style="color: #333; border-bottom: 1px solid #ddd; padding-bottom: 5px;">Registration Status</h4>
                    <p><strong>Status:</strong> <?php echo ucfirst(str_replace('_', ' ', $order->get_meta('_ivs_registration_status'))); ?></p>
                    <?php if ($order->get_meta('_ivs_company_crn')): ?>
                        <p><strong>Company CRN:</strong> <?php echo esc_html($order->get_meta('_ivs_company_crn')); ?></p>
                    <?php endif; ?>
                </div>
                
                <!-- Personal Information Section -->
                <div style="margin-bottom: 20px;">
                    <h4 style="color: #333; border-bottom: 1px solid #ddd; padding-bottom: 5px;">Personal Information</h4>
                    <p><strong>Director Name:</strong> <?php echo esc_html($director_name); ?></p>
                    <p><strong>Date of Birth:</strong> <?php echo esc_html($order->get_meta('_ivs_date_of_birth')); ?></p>
                    <p><strong>Passport/CNIC:</strong> <?php echo esc_html($order->get_meta('_ivs_passport_cnic')); ?></p>
                    <p><strong>Issuance Country:</strong> <?php echo esc_html($order->get_meta('_ivs_issuance_country')); ?></p>
                    <p><strong>Registered Email:</strong> <?php echo esc_html($order->get_meta('_ivs_registered_email')); ?></p>
                </div>
                
                <!-- Company Role Section -->
                <div style="margin-bottom: 20px;">
                    <h4 style="color: #333; border-bottom: 1px solid #ddd; padding-bottom: 5px;">Company Role</h4>
                    <p><strong>Role:</strong> <?php echo ucfirst($order->get_meta('_ivs_company_role')); ?></p>
                </div>
                
                <?php if ($order->get_meta('_ivs_has_second_director') === '1'): ?>
                <!-- Second Director / PSC Section -->
                <div style="margin-bottom: 20px;">
                    <h4 style="color: #333; border-bottom: 1px solid #ddd; padding-bottom: 5px;">Second Director / PSC Details</h4>
                    <p><strong>Director 2 Name:</strong> <?php echo esc_html($order->get_meta('_ivs_director2_name')); ?></p>
                    <p><strong>Date of Birth:</strong> <?php echo esc_html($order->get_meta('_ivs_director2_date_of_birth')); ?></p>
                    <p><strong>Passport/CNIC:</strong> <?php echo esc_html($order->get_meta('_ivs_director2_passport_cnic')); ?></p>
                    <p><strong>Issuance Country:</strong> <?php echo esc_html($order->get_meta('_ivs_director2_issuance_country')); ?></p>
                    <p><strong>Registered Email:</strong> <?php echo esc_html($order->get_meta('_ivs_director2_registered_email')); ?></p>
                    <p><strong>Role:</strong> <?php echo ucfirst($order->get_meta('_ivs_director2_company_role')); ?></p>
                    <p><strong>Street Address:</strong> <?php echo esc_html($order->get_meta('_ivs_director2_address_street')); ?></p>
                    <?php if ($order->get_meta('_ivs_director2_address_line2')): ?>
                        <p><strong>Address Line 2:</strong> <?php echo esc_html($order->get_meta('_ivs_director2_address_line2')); ?></p>
                    <?php endif; ?>
                    <p><strong>City:</strong> <?php echo esc_html($order->get_meta('_ivs_director2_address_city')); ?></p>
                    <p><strong>Zip/Postal Code:</strong> <?php echo esc_html($order->get_meta('_ivs_director2_address_zip')); ?></p>
                    <p><strong>Country:</strong> <?php echo esc_html($order->get_meta('_ivs_director2_address_country')); ?></p>
                </div>
                <?php endif; ?>
                
                <!-- Address Details Section -->
                <div style="margin-bottom: 20px;">
                    <h4 style="color: #333; border-bottom: 1px solid #ddd; padding-bottom: 5px;">Director Home Address</h4>
                    <p><strong>Street Address:</strong> <?php echo esc_html($order->get_meta('_ivs_address_street')); ?></p>
                    <?php if ($order->get_meta('_ivs_address_line2')): ?>
                        <p><strong>Address Line 2:</strong> <?php echo esc_html($order->get_meta('_ivs_address_line2')); ?></p>
                    <?php endif; ?>
                    <p><strong>City:</strong> <?php echo esc_html($order->get_meta('_ivs_address_city')); ?></p>
                    <p><strong>Zip/Postal Code:</strong> <?php echo esc_html($order->get_meta('_ivs_address_zip')); ?></p>
                    <p><strong>Country:</strong> <?php echo esc_html($order->get_meta('_ivs_address_country')); ?></p>
                </div>
                
                <!-- ACSP Compliance Questions Section -->
                <div style="margin-bottom: 20px;">
                    <h4 style="color: #333; border-bottom: 1px solid #ddd; padding-bottom: 5px;">ACSP COMPLIANCE QUESTIONS</h4>
                    
                    <div style="margin: 10px 0;">
                        <h5 style="margin: 0 0 5px 0; color: #555;">Purpose of Verification</h5>
                        <p><?php echo ucfirst(str_replace('_', ' ', $order->get_meta('_ivs_verification_purpose'))); ?></p>
                    </div>
                    
                    <div style="margin: 10px 0;">
                        <h5 style="margin: 0 0 5px 0; color: #555;">PEP Declaration</h5>
                        <p><strong>Are you a PEP?</strong> <?php echo ucfirst($order->get_meta('_ivs_is_pep')); ?></p>
                        <p><strong>PEP Associate?</strong> <?php echo ucfirst($order->get_meta('_ivs_pep_associate')); ?></p>
                    </div>
                    
                    <div style="margin: 10px 0;">
                        <h5 style="margin: 0 0 5px 0; color: #555;">Sanctions Declaration</h5>
                        <p><strong>Subject to sanctions?</strong> <?php echo ucfirst($order->get_meta('_ivs_subject_to_sanctions')); ?></p>
                        <p><strong>Financial crime conviction?</strong> <?php echo ucfirst($order->get_meta('_ivs_financial_crime')); ?></p>
                    </div>
                </div>
                
                <!-- Non-Refund Acknowledgement Section -->
                <div style="margin-bottom: 20px;">
                    <h4 style="color: #333; border-bottom: 1px solid #ddd; padding-bottom: 5px;">Non-Refund Acknowledgement</h4>
                    <p><strong>Acknowledged:</strong> <?php echo $order->get_meta('_ivs_non_refund_ack') ? 'Yes' : 'No'; ?></p>
                </div>
                
                <!-- Uploaded Documents Section -->
                <div style="margin-bottom: 20px;">
                    <h4 style="color: #333; border-bottom: 1px solid #ddd; padding-bottom: 5px;">Uploaded Documents</h4>
                    <div style="display: flex; flex-wrap: wrap; gap: 15px;">
                        <?php
                        // Display photo ID files
                        for ($i = 1; $i <= 5; $i++) {
                            $photo_id_url = $order->get_meta('_ivs_photo_id_' . $i . '_url');
                            $photo_id_name = $order->get_meta('_ivs_photo_id_' . $i . '_name');
                            if ($photo_id_url): ?>
                                <div style="background: white; padding: 10px; border-radius: 5px; border: 1px solid #ddd; min-width: 200px;">
                                    <strong>Photo ID <?php echo $i; ?>:</strong><br>
                                    <a href="<?php echo esc_url($photo_id_url); ?>" target="_blank" style="color: #0073aa; text-decoration: underline;">
                                        <?php echo esc_html($photo_id_name ?: 'View File'); ?>
                                    </a>
                                </div>
                            <?php endif;
                        }
                        
                        // Display selfie files
                        for ($i = 1; $i <= 5; $i++) {
                            $selfie_url = $order->get_meta('_ivs_selfie_id_' . $i . '_url');
                            $selfie_name = $order->get_meta('_ivs_selfie_id_' . $i . '_name');
                            if ($selfie_url): ?>
                                <div style="background: white; padding: 10px; border-radius: 5px; border: 1px solid #ddd; min-width: 200px;">
                                    <strong>Selfie with ID <?php echo $i; ?>:</strong><br>
                                    <a href="<?php echo esc_url($selfie_url); ?>" target="_blank" style="color: #0073aa; text-decoration: underline;">
                                        <?php echo esc_html($selfie_name ?: 'View File'); ?>
                                    </a>
                                </div>
                            <?php endif;
                        }
                        
                        // Display address proof files
                        for ($i = 1; $i <= 5; $i++) {
                            $address_url = $order->get_meta('_ivs_address_proof_' . $i . '_url');
                            $address_name = $order->get_meta('_ivs_address_proof_' . $i . '_name');
                            if ($address_url): ?>
                                <div style="background: white; padding: 10px; border-radius: 5px; border: 1px solid #ddd; min-width: 200px;">
                                    <strong>Address Proof <?php echo $i; ?>:</strong><br>
                                    <a href="<?php echo esc_url($address_url); ?>" target="_blank" style="color: #0073aa; text-decoration: underline;">
                                        <?php echo esc_html($address_name ?: 'View File'); ?>
                                    </a>
                                </div>
                            <?php endif;
                        }
                        for ($i = 1; $i <= 5; $i++) {
                            $d2_photo_url = $order->get_meta('_ivs_director2_photo_id_' . $i . '_url');
                            $d2_photo_name = $order->get_meta('_ivs_director2_photo_id_' . $i . '_name');
                            if ($d2_photo_url): ?>
                                <div style="background: white; padding: 10px; border-radius: 5px; border: 1px solid #ddd; min-width: 200px;">
                                    <strong>Director 2 Photo ID <?php echo $i; ?>:</strong><br>
                                    <a href="<?php echo esc_url($d2_photo_url); ?>" target="_blank" style="color: #0073aa; text-decoration: underline;">
                                        <?php echo esc_html($d2_photo_name ?: 'View File'); ?>
                                    </a>
                                </div>
                            <?php endif;
                        }
                        for ($i = 1; $i <= 5; $i++) {
                            $d2_addr_url = $order->get_meta('_ivs_director2_address_proof_' . $i . '_url');
                            $d2_addr_name = $order->get_meta('_ivs_director2_address_proof_' . $i . '_name');
                            if ($d2_addr_url): ?>
                                <div style="background: white; padding: 10px; border-radius: 5px; border: 1px solid #ddd; min-width: 200px;">
                                    <strong>Director 2 Address Proof <?php echo $i; ?>:</strong><br>
                                    <a href="<?php echo esc_url($d2_addr_url); ?>" target="_blank" style="color: #0073aa; text-decoration: underline;">
                                        <?php echo esc_html($d2_addr_name ?: 'View File'); ?>
                                    </a>
                                </div>
                            <?php endif;
                        }
                        ?>
                    </div>
                </div>
                
                <!-- Submission Details -->
                <div style="margin-top: 20px; padding-top: 15px; border-top: 1px solid #ddd;">
                    <h4 style="color: #333; margin-bottom: 10px;">Submission Details</h4>
                    <p><strong>Submitted:</strong> <?php echo date('F j, Y g:i a', $order->get_meta('_ivs_timestamp')); ?></p>
                </div>
            </div>
            <?php
        }
    }
    
    public function add_to_order_emails($order, $sent_to_admin, $plain_text, $email) {
        $director_name = $order->get_meta('_ivs_director_name');
        
        if ($director_name) {
            if ($plain_text) {
                echo "\n\nIDENTITY VERIFICATION SERVICE DETAILS:\n";
                echo "=====================================\n";
                
                echo "REGISTRATION STATUS:\n";
                echo "Status: " . ucfirst(str_replace('_', ' ', $order->get_meta('_ivs_registration_status'))) . "\n";
                if ($order->get_meta('_ivs_company_crn')) {
                    echo "Company CRN: " . $order->get_meta('_ivs_company_crn') . "\n";
                }
                
                echo "\nPERSONAL INFORMATION:\n";
                echo "Director Name: " . $director_name . "\n";
                echo "Date of Birth: " . $order->get_meta('_ivs_date_of_birth') . "\n";
                echo "Passport/CNIC: " . $order->get_meta('_ivs_passport_cnic') . "\n";
                echo "Issuance Country: " . $order->get_meta('_ivs_issuance_country') . "\n";
                echo "Registered Email: " . $order->get_meta('_ivs_registered_email') . "\n";
                
                echo "\nCOMPANY ROLE:\n";
                echo "Role: " . ucfirst($order->get_meta('_ivs_company_role')) . "\n";
                
                echo "\nADDRESS DETAILS:\n";
                echo "Street Address: " . $order->get_meta('_ivs_address_street') . "\n";
                if ($order->get_meta('_ivs_address_line2')) {
                    echo "Address Line 2: " . $order->get_meta('_ivs_address_line2') . "\n";
                }
                echo "City: " . $order->get_meta('_ivs_address_city') . "\n";
                echo "Zip/Postal Code: " . $order->get_meta('_ivs_address_zip') . "\n";
                echo "Country: " . $order->get_meta('_ivs_address_country') . "\n";
                
                echo "\nACSP COMPLIANCE QUESTIONS:\n";
                echo "Verification Purpose: " . ucfirst(str_replace('_', ' ', $order->get_meta('_ivs_verification_purpose'))) . "\n";
                echo "Are you a PEP?: " . ucfirst($order->get_meta('_ivs_is_pep')) . "\n";
                echo "PEP Associate?: " . ucfirst($order->get_meta('_ivs_pep_associate')) . "\n";
                echo "Subject to sanctions?: " . ucfirst($order->get_meta('_ivs_subject_to_sanctions')) . "\n";
                echo "Financial crime conviction?: " . ucfirst($order->get_meta('_ivs_financial_crime')) . "\n";
                
                echo "\nSOURCE OF FUNDS (SOF):\n";
                $sof = maybe_unserialize($order->get_meta('_ivs_source_of_funds'));
                if (is_array($sof) && !empty($sof)) {
                    foreach ($sof as $fund) {
                        echo "- " . ucfirst(str_replace('_', ' ', $fund)) . "\n";
                    }
                } else {
                    echo "Not specified\n";
                }
                
                echo "\nNON-REFUND ACKNOWLEDGEMENT:\n";
                echo "Acknowledged: " . ($order->get_meta('_ivs_non_refund_ack') ? 'Yes' : 'No') . "\n";
                
                echo "\nDOCUMENTS UPLOADED:\n";
                echo "Photo ID: " . ($order->get_meta('_ivs_photo_id_1_url') ? 'Uploaded' : 'Not uploaded') . "\n";
                echo "Selfie with ID: " . ($order->get_meta('_ivs_selfie_id_1_url') ? 'Uploaded' : 'Not uploaded') . "\n";
                echo "Address Proof: " . ($order->get_meta('_ivs_address_proof_1_url') ? 'Uploaded' : 'Not uploaded') . "\n";
                
                echo "\nSubmitted: " . date('F j, Y g:i a', $order->get_meta('_ivs_timestamp')) . "\n";
            } else {
                ?>
                <div style="margin: 20px 0; padding: 15px; border: 1px solid #ddd; background: #f9f9f9;">
                    <h3 style="color: #0073aa; margin-top: 0; border-bottom: 2px solid #0073aa; padding-bottom: 10px;">Identity Verification Service Details</h3>
                    
                    <!-- Registration Status -->
                    <div style="margin-bottom: 15px;">
                        <h4 style="color: #333; margin-bottom: 10px;">Registration Status</h4>
                        <p><strong>Status:</strong> <?php echo ucfirst(str_replace('_', ' ', $order->get_meta('_ivs_registration_status'))); ?></p>
                        <?php if ($order->get_meta('_ivs_company_crn')): ?>
                            <p><strong>Company CRN:</strong> <?php echo esc_html($order->get_meta('_ivs_company_crn')); ?></p>
                        <?php endif; ?>
                    </div>
                    
                    <!-- Personal Information -->
                    <div style="margin-bottom: 15px;">
                        <h4 style="color: #333; margin-bottom: 10px;">Personal Information</h4>
                        <p><strong>Director Name:</strong> <?php echo esc_html($director_name); ?></p>
                        <p><strong>Date of Birth:</strong> <?php echo esc_html($order->get_meta('_ivs_date_of_birth')); ?></p>
                        <p><strong>Passport/CNIC:</strong> <?php echo esc_html($order->get_meta('_ivs_passport_cnic')); ?></p>
                        <p><strong>Issuance Country:</strong> <?php echo esc_html($order->get_meta('_ivs_issuance_country')); ?></p>
                        <p><strong>Registered Email:</strong> <?php echo esc_html($order->get_meta('_ivs_registered_email')); ?></p>
                    </div>
                    
                    <!-- Company Role -->
                    <div style="margin-bottom: 15px;">
                        <h4 style="color: #333; margin-bottom: 10px;">Company Role</h4>
                        <p><strong>Role:</strong> <?php echo ucfirst($order->get_meta('_ivs_company_role')); ?></p>
                    </div>
                    
                    <!-- Address Details -->
                    <div style="margin-bottom: 15px;">
                        <h4 style="color: #333; margin-bottom: 10px;">Director Home Address</h4>
                        <p><strong>Street Address:</strong> <?php echo esc_html($order->get_meta('_ivs_address_street')); ?></p>
                        <?php if ($order->get_meta('_ivs_address_line2')): ?>
                            <p><strong>Address Line 2:</strong> <?php echo esc_html($order->get_meta('_ivs_address_line2')); ?></p>
                        <?php endif; ?>
                        <p><strong>City:</strong> <?php echo esc_html($order->get_meta('_ivs_address_city')); ?></p>
                        <p><strong>Zip/Postal Code:</strong> <?php echo esc_html($order->get_meta('_ivs_address_zip')); ?></p>
                        <p><strong>Country:</strong> <?php echo esc_html($order->get_meta('_ivs_address_country')); ?></p>
                    </div>
                    
                    <!-- ACSP Compliance -->
                    <div style="margin-bottom: 15px;">
                        <h4 style="color: #333; margin-bottom: 10px;">ACSP COMPLIANCE QUESTIONS</h4>
                        <p><strong>Purpose of Verification:</strong> <?php echo ucfirst(str_replace('_', ' ', $order->get_meta('_ivs_verification_purpose'))); ?></p>
                        <p><strong>Are you a PEP?:</strong> <?php echo ucfirst($order->get_meta('_ivs_is_pep')); ?></p>
                        <p><strong>PEP Associate?:</strong> <?php echo ucfirst($order->get_meta('_ivs_pep_associate')); ?></p>
                        <p><strong>Subject to sanctions?:</strong> <?php echo ucfirst($order->get_meta('_ivs_subject_to_sanctions')); ?></p>
                        <p><strong>Financial crime conviction?:</strong> <?php echo ucfirst($order->get_meta('_ivs_financial_crime')); ?></p>
                    </div>
                    
                    <!-- Source of Funds -->
                    <div style="margin-bottom: 15px;">
                        <h4 style="color: #333; margin-bottom: 10px;">SOURCE OF FUNDS (SOF)</h4>
                        <?php
                        $sof = maybe_unserialize($order->get_meta('_ivs_source_of_funds'));
                        if (is_array($sof) && !empty($sof)) {
                            echo '<ul style="margin: 10px 0; padding-left: 20px;">';
                            foreach ($sof as $fund) {
                                echo '<li>' . ucfirst(str_replace('_', ' ', $fund)) . '</li>';
                            }
                            echo '</ul>';
                        } else {
                            echo '<p>Not specified</p>';
                        }
                        ?>
                    </div>
                    
                    <!-- Non-Refund Acknowledgement -->
                    <div style="margin-bottom: 15px;">
                        <h4 style="color: #333; margin-bottom: 10px;">Non-Refund Acknowledgement</h4>
                        <p><strong>Acknowledged:</strong> <?php echo $order->get_meta('_ivs_non_refund_ack') ? 'Yes' : 'No'; ?></p>
                    </div>
                    
                    <!-- Submission Details -->
                    <div style="margin-top: 15px; border-top: 1px solid #ddd; padding-top: 10px;">
                        <p><strong>Submitted:</strong> <?php echo date('F j, Y g:i a', $order->get_meta('_ivs_timestamp')); ?></p>
                    </div>
                </div>
                <?php
            }
        }
    }
    
    // ==================== MY ACCOUNT TAB ====================
    
    public function add_myaccount_tab($items) {
        if (is_user_logged_in()) {
            $user_id = get_current_user_id();
            
            // Check if user has purchased the specific product
            $has_purchased = wc_customer_bought_product($user_id, $user_id, $this->product_id);
            
            if ($has_purchased) {
                $new_items = array();
                foreach ($items as $key => $value) {
                    $new_items[$key] = $value;
                    if ($key === 'orders') {
                        $new_items['identity-verification'] = 'Identity Verification';
                    }
                }
                return $new_items;
            }
        }
        
        return $items;
    }
    
    public function add_endpoint() {
        add_rewrite_endpoint('identity-verification', EP_ROOT | EP_PAGES);
    }
    
    public function add_query_vars($vars) {
        $vars[] = 'identity-verification';
        return $vars;
    }
    
    public function tab_content() {
        $user_id = get_current_user_id();
        $has_purchased = wc_customer_bought_product($user_id, $user_id, $this->product_id);
        
        if ($has_purchased) {
            $orders = wc_get_orders(array(
                'customer_id' => $user_id,
                'meta_key' => '_ivs_director_name',
                'meta_compare' => 'EXISTS',
                'return' => 'ids'
            ));
            
            if ($orders) {
                echo '<h2>Your Identity Verification Services</h2>';
                
                foreach ($orders as $order_id) {
                    $order = wc_get_order($order_id);
                    $director_name = $order->get_meta('_ivs_director_name');
                    
                    if ($director_name) {
                        ?>
                        <div class="ivs-service-card" style="background: #f9f9f9; padding: 20px; margin-bottom: 20px; border-radius: 8px; border: 1px solid #00000069; margin-top: 40px;">
                            <h3 style="margin-top: 0; color: #003971; border-bottom: 1px solid #ddd; padding-bottom: 10px;font-size: 25px; font-family: 'Red Hat Display'; font-weight: 600;">
                                Order #<?php echo $order_id; ?> - <?php echo $order->get_date_created()->format('d M Y'); ?>
                            </h3>
                            
                            <!-- Registration Status -->
                            <div style="margin-bottom: 15px;">
                                <h4 style="margin: 0 0 10px 0; color: #333;">Registration Status</h4>
                                <p><strong>Status:</strong> <?php echo ucfirst(str_replace('_', ' ', $order->get_meta('_ivs_registration_status'))); ?></p>
                                <?php if ($order->get_meta('_ivs_company_crn')): ?>
                                    <p><strong>Company CRN:</strong> <?php echo esc_html($order->get_meta('_ivs_company_crn')); ?></p>
                                <?php endif; ?>
                            </div>
                            
                            <!-- Personal Information -->
                            <div style="margin-bottom: 15px;">
                                <h4 style="margin: 0 0 10px 0; color: #333;">Personal Information</h4>
                                <p><strong>Director Name:</strong> <?php echo esc_html($director_name); ?></p>
                                <p><strong>Date of Birth:</strong> <?php echo esc_html($order->get_meta('_ivs_date_of_birth')); ?></p>
                                <p><strong>Passport/CNIC:</strong> <?php echo esc_html($order->get_meta('_ivs_passport_cnic')); ?></p>
                                <p><strong>Issuance Country:</strong> <?php echo esc_html($order->get_meta('_ivs_issuance_country')); ?></p>
                                <p><strong>Registered Email:</strong> <?php echo esc_html($order->get_meta('_ivs_registered_email')); ?></p>
                            </div>
                            
                            <!-- Company Role -->
                            <div style="margin-bottom: 15px;">
                                <h4 style="margin: 0 0 10px 0; color: #333;">Company Details</h4>
                                <p><strong>Role:</strong> <?php echo ucfirst($order->get_meta('_ivs_company_role')); ?></p>
                            </div>
                            
                            <!-- Address Details -->
                            <div style="margin-bottom: 15px;">
                                <h4 style="margin: 0 0 10px 0; color: #333;">Director Home Address</h4>
                                <p><strong>Street Address:</strong> <?php echo esc_html($order->get_meta('_ivs_address_street')); ?></p>
                                <?php if ($order->get_meta('_ivs_address_line2')): ?>
                                    <p><strong>Address Line 2:</strong> <?php echo esc_html($order->get_meta('_ivs_address_line2')); ?></p>
                                <?php endif; ?>
                                <p><strong>City:</strong> <?php echo esc_html($order->get_meta('_ivs_address_city')); ?></p>
                                <p><strong>Zip/Postal Code:</strong> <?php echo esc_html($order->get_meta('_ivs_address_zip')); ?></p>
                                <p><strong>Country:</strong> <?php echo esc_html($order->get_meta('_ivs_address_country')); ?></p>
                            </div>
                            
                            <!-- ACSP Compliance -->
                            <div style="margin-bottom: 15px;">
                                <h4 style="margin: 0 0 10px 0; color: #333;">ACSP Compliance</h4>
                                <p><strong>Purpose:</strong> <?php echo ucfirst(str_replace('_', ' ', $order->get_meta('_ivs_verification_purpose'))); ?></p>
                                <p><strong>PEP Status:</strong> <?php echo ucfirst($order->get_meta('_ivs_is_pep')); ?></p>
                                <p><strong>PEP Associate:</strong> <?php echo ucfirst($order->get_meta('_ivs_pep_associate')); ?></p>
                                <p><strong>Sanctions:</strong> <?php echo ucfirst($order->get_meta('_ivs_subject_to_sanctions')); ?></p>
                                <p><strong>Financial Crime:</strong> <?php echo ucfirst($order->get_meta('_ivs_financial_crime')); ?></p>
                            </div>
                            
                            <!-- Source of Funds -->
                            <div style="margin-bottom: 15px;">
                                <h4 style="margin: 0 0 10px 0; color: #333;">Source of Funds</h4>
                                <?php
                                $sof = maybe_unserialize($order->get_meta('_ivs_source_of_funds'));
                                if (is_array($sof) && !empty($sof)) {
                                    echo '<ul style="margin: 10px 0; padding-left: 20px;">';
                                    foreach ($sof as $fund) {
                                        echo '<li>' . ucfirst(str_replace('_', ' ', $fund)) . '</li>';
                                    }
                                    echo '</ul>';
                                } else {
                                    echo '<p>Not specified</p>';
                                }
                                ?>
                            </div>
                            
                            <!-- Non-Refund Acknowledgement -->
                            <div style="margin-bottom: 15px;">
                                <h4 style="margin: 0 0 10px 0; color: #333;">Non-Refund Acknowledgement</h4>
                                <p><strong>Acknowledged:</strong> <?php echo $order->get_meta('_ivs_non_refund_ack') ? 'Yes' : 'No'; ?></p>
                            </div>
                            
                            <!-- Uploaded Documents -->
                            <div style="margin-bottom: 15px;">
                                <h4 style="margin: 0 0 10px 0; color: #333;">Uploaded Documents</h4>
                                <div style="display: flex; flex-wrap: wrap; gap: 15px;">
                                    <?php
                                    // Display uploaded files
                                    $file_types = array('photo_id', 'address_proof', 'director2_photo_id', 'director2_address_proof');
                                    $file_labels = array(
                                        'photo_id' => 'Photo ID',
                                        'address_proof' => 'Address Proof',
                                        'director2_photo_id' => 'Director 2 Photo ID',
                                        'director2_address_proof' => 'Director 2 Address Proof'
                                    );
                                    
                                    foreach ($file_types as $file_type) {
                                        for ($i = 1; $i <= 5; $i++) {
                                            $file_url = $order->get_meta('_ivs_' . $file_type . '_' . $i . '_url');
                                            $file_name = $order->get_meta('_ivs_' . $file_type . '_' . $i . '_name');
                                            if ($file_url): ?>
                                                <div style="background: white; padding: 10px; border-radius: 5px; border: 1px solid #ddd; min-width: 200px;">
                                                    <strong><?php echo esc_html($file_labels[$file_type]); ?> <?php echo $i; ?>:</strong><br>
                                                    <a href="<?php echo esc_url($file_url); ?>" target="_blank" style="color: #0073aa; text-decoration: none;">
                                                        📄 <?php echo esc_html($file_name ?: 'View File'); ?>
                                                    </a>
                                                </div>
                                            <?php endif;
                                        }
                                    }
                                    ?>
                                </div>
                            </div>
                            
                            <!-- Footer Section -->
                            <div style="display: flex; justify-content: space-between; align-items: center; border-top: 1px solid #ddd; padding-top: 15px;">
                                <div>
                                    <p style="margin: 0;"><strong>Status:</strong> <span style="color: #007c21; font-weight: bold;"><?php echo wc_get_order_status_name($order->get_status()); ?></span></p>
                                    <p style="margin: 0;"><strong>Submitted:</strong> <?php echo date('F j, Y g:i a', $order->get_meta('_ivs_timestamp')); ?></p>
                                </div>
                                <a href="<?php echo $order->get_view_order_url(); ?>" class="button" style="background-image: linear-gradient(107deg, #003971 20%, var(--e-global-color-6d89c38) 100%); color: white; border: none; padding: 0px 20px; border-radius: 4px; text-decoration: none;">View Order</a>
                            </div>
                        </div>
                        <?php
                    }
                }
            } else {
                echo '<p>No identity verification service orders found.</p>';
            }
        } else {
            echo '<p>You have not purchased any Identity Verification services yet.</p>';
        }
    }
    
    // ==================== HELPER FUNCTIONS ====================
    
    public function get_countries_options() {
        $countries = array(
            'GB' => 'United Kingdom',
            'US' => 'United States',
            'CA' => 'Canada',
            'AU' => 'Australia',
            'IN' => 'India',
            'PK' => 'Pakistan',
            'BD' => 'Bangladesh',
            'CN' => 'China',
            'JP' => 'Japan',
            'KR' => 'South Korea',
            'SG' => 'Singapore',
            'MY' => 'Malaysia',
            'AE' => 'United Arab Emirates',
            'SA' => 'Saudi Arabia',
            'QA' => 'Qatar',
            'KW' => 'Kuwait',
            'OM' => 'Oman',
            'BH' => 'Bahrain',
            'DE' => 'Germany',
            'FR' => 'France',
            'IT' => 'Italy',
            'ES' => 'Spain',
            'NL' => 'Netherlands',
            'BE' => 'Belgium',
            'CH' => 'Switzerland',
            'SE' => 'Sweden',
            'NO' => 'Norway',
            'DK' => 'Denmark',
            'FI' => 'Finland',
            'AT' => 'Austria',
            'IE' => 'Ireland',
            'PT' => 'Portugal',
            'GR' => 'Greece',
            'PL' => 'Poland',
            'CZ' => 'Czech Republic',
            'HU' => 'Hungary',
            'RO' => 'Romania',
            'BG' => 'Bulgaria',
            'RS' => 'Serbia',
            'HR' => 'Croatia',
            'SI' => 'Slovenia',
            'SK' => 'Slovakia',
            'LT' => 'Lithuania',
            'LV' => 'Latvia',
            'EE' => 'Estonia',
            'MT' => 'Malta',
            'CY' => 'Cyprus',
            'LU' => 'Luxembourg',
            'IS' => 'Iceland',
            'ZA' => 'South Africa',
            'NG' => 'Nigeria',
            'KE' => 'Kenya',
            'GH' => 'Ghana',
            'EG' => 'Egypt',
            'MA' => 'Morocco',
            'TN' => 'Tunisia',
            'DZ' => 'Algeria',
            'LY' => 'Libya',
            'SD' => 'Sudan',
            'ET' => 'Ethiopia',
            'TZ' => 'Tanzania',
            'UG' => 'Uganda',
            'MZ' => 'Mozambique',
            'ZM' => 'Zambia',
            'ZW' => 'Zimbabwe',
            'BW' => 'Botswana',
            'NA' => 'Namibia',
            'MG' => 'Madagascar',
            'MU' => 'Mauritius',
            'SC' => 'Seychelles',
            'RW' => 'Rwanda',
            'BI' => 'Burundi',
            'SL' => 'Sierra Leone',
            'LR' => 'Liberia',
            'CI' => 'Ivory Coast',
            'SN' => 'Senegal',
            'ML' => 'Mali',
            'BF' => 'Burkina Faso',
            'NE' => 'Niger',
            'TG' => 'Togo',
            'BJ' => 'Benin',
            'CM' => 'Cameroon',
            'CD' => 'DR Congo',
            'CG' => 'Republic of the Congo',
            'GA' => 'Gabon',
            'GQ' => 'Equatorial Guinea',
            'ST' => 'São Tomé and Príncipe',
            'AO' => 'Angola',
            'NA' => 'Namibia',
        );
        
        $options = '';
        foreach ($countries as $code => $name) {
            $options .= '<option value="' . esc_attr($name) . '">' . esc_html($name) . '</option>';
        }
        
        return $options;
    }
    
    // ==================== ADMIN SETTINGS ====================
    
    public function add_admin_menu() {
        add_menu_page(
            'Identity Verification Settings',
            'Identity Verification',
            'manage_options',
            'ivs-settings',
            array($this, 'settings_page'),
            'dashicons-id',
            33
        );
    }
    
    public function settings_page() {
        $settings = get_option('ivs_settings', array());
        ?>
        <div class="wrap">
            <h1>Identity Verification Service Settings</h1>
            
            <form method="post" action="options.php">
                <?php settings_fields('ivs_settings_group'); ?>
                
                <table class="form-table">
                    <tr>
                        <th scope="row">Product ID</th>
                        <td>
                            <input type="number" name="ivs_settings[product_id]" 
                                   value="<?php echo esc_attr(isset($settings['product_id']) ? $settings['product_id'] : $this->product_id); ?>"
                                   class="regular-text" required>
                            <p class="description">Enter the WooCommerce product ID for the Identity Verification Service</p>
                        </td>
                    </tr>
                    
                    <tr>
                        <th scope="row">Primary Color</th>
                        <td>
                            <input type="color" name="ivs_settings[primary_color]" 
                                   value="<?php echo esc_attr(isset($settings['primary_color']) ? $settings['primary_color'] : '#0073aa'); ?>">
                        </td>
                    </tr>
                    
                    <tr>
                        <th scope="row">Button Color</th>
                        <td>
                            <p>Normal: <input type="color" name="ivs_settings[button_color]" 
                                   value="<?php echo esc_attr(isset($settings['button_color']) ? $settings['button_color'] : '#0073aa'); ?>"></p>
                            <p>Hover: <input type="color" name="ivs_settings[button_hover]" 
                                   value="<?php echo esc_attr(isset($settings['button_hover']) ? $settings['button_hover'] : '#005177'); ?>"></p>
                        </td>
                    </tr>
                    
                    <tr>
                        <th scope="row">Font Family</th>
                        <td>
                            <select name="ivs_settings[font_family]" class="regular-text">
                                <?php
                                $fonts = array(
                                    'Arial, sans-serif',
                                    'Helvetica, sans-serif',
                                    'Verdana, sans-serif',
                                    'Georgia, serif',
                                    'Times New Roman, serif',
                                    'Courier New, monospace',
                                    'Tahoma, sans-serif',
                                    'Trebuchet MS, sans-serif'
                                );
                                
                                foreach ($fonts as $font) {
                                    $selected = (isset($settings['font_family']) && $settings['font_family'] == $font) ? 'selected' : '';
                                    echo '<option value="' . esc_attr($font) . '" ' . $selected . '>' . esc_html($font) . '</option>';
                                }
                                ?>
                            </select>
                        </td>
                    </tr>
                    
                    <tr>
                        <th scope="row">Form Shortcode</th>
                        <td>
                            <code>[identity_verification_form]</code>
                            <p class="description">Use this shortcode to display the form on any page</p>
                        </td>
                    </tr>
                </table>
                
                <?php submit_button(); ?>
            </form>
        </div>
        <?php
    }
    
    public function register_settings() {
        register_setting('ivs_settings_group', 'ivs_settings');
    }
    
    // ==================== STYLES AND SCRIPTS ====================
    
    public function enqueue_scripts() {
        wp_enqueue_style('ivs-frontend-style', false);
        wp_add_inline_style('ivs-frontend-style', $this->get_dynamic_css());
        wp_enqueue_script('jquery');
    }
    
    public function admin_scripts($hook) {
        if ('toplevel_page_ivs-settings' === $hook) {
            wp_enqueue_style('wp-color-picker');
            wp_enqueue_script('wp-color-picker');
        }
    }
    
    public function dynamic_css() {
        echo '<style>' . $this->get_dynamic_css() . '</style>';
    }
    
    private function get_dynamic_css() {
        $settings = get_option('ivs_settings', array());
        $primary_color = isset($settings['primary_color']) ? $settings['primary_color'] : '#0073aa';
        $button_color = isset($settings['button_color']) ? $settings['button_color'] : '#0073aa';
        $button_hover = isset($settings['button_hover']) ? $settings['button_hover'] : '#005177';
        $font_family = isset($settings['font_family']) ? $settings['font_family'] : 'Arial, sans-serif';
        
        return "
        .ivs-form-container {
            max-width: 1000px;
            margin: 0;
            padding: 10px;
            background: #fff;
            border-radius: 10px;
            box-shadow: 0 1px 15px #0000004d;
            margin-left: 50px;
            margin-top: 30px;
            padding-bottom: 30px;
            font-family: {$font_family};
        }
        
        .ivs-heading {
            color: {$primary_color};
            border-bottom: none;
            padding-bottom: 0px;
            margin-bottom: 30px;
            font-size: 20px;
            font-weight: 700;
        }
        
        .ivs-subheading {
            color: #242424;
            margin-bottom: 18px;
            font-weight: 600;
            font-size: 22px;
            margin-top: 20px;
        }
        
        .ivs-question {
            color: #000000;
            margin: 20px 0 15px 0;
            font-weight: 500;
            font-size: 19px;
        }
        
        .ivs-form-section {
            margin-bottom: 10px;
            padding: 20px;
            background: #f9f9f9;
            border-radius: 8px;
        }
        
        .ivs-form-row {
            display: flex;
            flex-wrap: wrap;
            gap: 25px;
            margin-bottom: 20px;
        }
        
        .ivs-form-col {
            flex: 1;
            min-width: 250px;
        }
        
        .ivs-label {
            display: block;
            margin-bottom: 5px;
            font-weight: bold;
            color: #333;
        }
        
        .ivs-required {
            color: #ff0000;
            font-weight: bold;
        }
        
        .ivs-input, .ivs-file-input, .ivs-select {
            width: 100%;
            padding: 10px;
            border: 1px solid #ddd;
            border-radius: 4px;
            font-size: 14px;
            box-sizing: border-box;
        }
        
        .ivs-input:focus, .ivs-file-input:focus, .ivs-select:focus {
            border-color: {$primary_color};
            outline: none;
            box-shadow: 0 0 0 2px rgba(0, 115, 170, 0.2);
        }
        
        .ivs-submit-btn {
            background-image: linear-gradient(107deg, #003971 20%, var(--e-global-color-6d89c38) 100%);
            color: white;
            border: none;
            padding: 2px 60px;
            font-size: 20px;
            border-radius: 5px;
            cursor: pointer;
            transition: all 0.3s;
            font-weight: bold;
            display: inline-block;
        }
        
        .ivs-submit-btn:hover {
            background: {$button_hover};
            transform: translateY(-2px);
            box-shadow: 0 5px 15px rgba(0,0,0,0.2);
        }
        
        .ivs-submit-btn:disabled {
            background: #ccc;
            cursor: not-allowed;
            transform: none;
            box-shadow: none;
        }
        
        .ivs-submit-section {
            text-align: center;
            margin-top: 30px;
        }
        
        .ivs-radio-group, .ivs-checkbox-group {
            display: flex;
            flex-direction: column;
            gap: 10px;
        }
        
        .ivs-radio-label, .ivs-checkbox-label {
            display: flex;
            align-items: flex-start;
            padding: 2px;
            background: white;
            border: none;
            border-radius: 6px;
            cursor: pointer;
            transition: all 0.3s;
            color: #000000;
            font-size: 17px;
            font-weight: 500;
        }
        
        .ivs-radio-label:hover, .ivs-checkbox-label:hover {
            border-color: {$primary_color};
            background: #f0f8ff;
        }
        
        .ivs-radio-label input[type='radio'], .ivs-checkbox-label input[type='checkbox'] {
            margin-top: 6px;
            margin-right: 13px;
            transform: scale(1.2);
            accent-color: #003971;
        }
        
        .ivs-radio-text, .ivs-checkbox-text {
            flex: 1;
        }
        
        .ivs-divider {
            border: 0;
            height: 1px;
            background: #ddd;
            margin: 15px 0;
        }
        
        .ivs-file-hint {
            color: #000000;
            font-size: 14px;
            margin-top: 5px;
            padding: 10px;
            background: #fff;
            border-radius: 4px;
            padding-left: 3px;
        }
        
        .ivs-file-hint p {
            margin: 5px 0;
        }
        
        .ivs-file-hint em {
            font-style: italic;
            color: #888;
        }

        .ivs-second-director-section {
            margin-top: 18px;
            padding-top: 8px;
        }
        
        /* Mobile Responsive */
        @media (max-width: 768px) {
            .ivs-form-container { padding: 20px; }
            .ivs-form-col { min-width: 100%; }
            
            .ivs-form-col {
                width: 100% !important;
                min-width: 100% !important;
                flex: 0 0 100% !important;
            }
            
            .ivs-form-row {
                flex-direction: column;
            }
        }
        ";
    }
    
    private function get_frontend_js() {
        return <<<'JS'
        jQuery(document).ready(function($) {
            // Show/hide CRN field based on registration status
            $('input[name="registration_status"]').on('change', function() {
                if ($(this).val() === 'registered_elsewhere') {
                    $('#crn-field-container').slideDown(300);
                    $('#crn-field-container input').prop('required', true);
                } else {
                    $('#crn-field-container').slideUp(300);
                    $('#crn-field-container input').prop('required', false).val('');
                }
            });
            
            // Show/hide second director section based on checkbox
            $('#ivs-has-second-director').on('change', function() {
                var $sec = $('#ivs-second-director-section');
                if ($(this).is(':checked')) {
                    $sec.prop('hidden', false).slideDown(300);
                    $sec.find('input[type="text"], input[type="date"], input[type="email"], select').prop('required', true);
                    $sec.find('input[name="director2_address_line2"]').prop('required', false);
                    $sec.find('input[name="director2_company_role"]').first().prop('required', true);
                    $sec.find('.ivs-second-director-file').prop('required', true);
                } else {
                    $sec.slideUp(300, function() {
                        $(this).prop('hidden', true);
                    });
                    $sec.find('input, select').prop('required', false);
                }
            });
            
            // Form submission
            $('#ivs-verification-form').on('submit', function(e) {
                e.preventDefault();
                
                // Validate form
                var isValid = true;
                var errorMessage = '';
                
                // Check required fields
                $(this).find('[required]').each(function() {
                    if ($(this).is('input[type="file"]')) {
                        // For file inputs, check if file is selected
                        if ($(this).val() === '') {
                            isValid = false;
                            errorMessage = 'Please upload all required files';
                            $(this).css('border-color', '#ff0000');
                        } else {
                            $(this).css('border-color', '#ddd');
                        }
                    } else if ($(this).is('input[type="checkbox"]')) {
                        // For checkboxes
                        if (!$(this).is(':checked')) {
                            isValid = false;
                            errorMessage = 'Please check the required checkbox';
                            $(this).closest('.ivs-checkbox-label').css('border-color', '#ff0000');
                        } else {
                            $(this).closest('.ivs-checkbox-label').css('border-color', '#ddd');
                        }
                    } else if (!$(this).val().trim()) {
                        isValid = false;
                        var fieldName = $(this).prev('label').text() || $(this).attr('name');
                        errorMessage = 'Please fill all required fields';
                        $(this).css('border-color', '#ff0000');
                    } else {
                        $(this).css('border-color', '#ddd');
                    }
                });
                
                if (!isValid) {
                    alert(errorMessage);
                    return false;
                }
                
                // Show loading
                $('#ivs-loading').show();
                $('#ivs-submit-form').prop('disabled', true).text('Processing...');
                
                // Create FormData for file upload
                var formData = new FormData(this);
                formData.append('action', 'ivs_process_form');
                
                // Submit via AJAX
                $.ajax({
                    url: ivs_ajax.ajax_url,
                    type: 'POST',
                    data: formData,
                    processData: false,
                    contentType: false,
                    success: function(response) {
                        if (response.success && response.data.redirect) {
                            window.location.href = response.data.redirect;
                        } else {
                            alert('Error: ' + (response.data.message || 'Unknown error'));
                            $('#ivs-loading').hide();
                            $('#ivs-submit-form').prop('disabled', false).text('Submit Verification');
                        }
                    },
                    error: function() {
                        alert('An error occurred. Please try again.');
                        $('#ivs-loading').hide();
                        $('#ivs-submit-form').prop('disabled', false).text('Submit Verification');
                    }
                });
                
                return false;
            });
        });
JS;
    }
} // end class IdentityVerificationService
} // end class_exists check

// Initialize plugin
if (class_exists('IdentityVerificationService')) {
    IdentityVerificationService::get_instance();
}