# -*- coding: utf-8 -*-

import json
import uuid
import time
from datetime import datetime

from odoo import models, api, _
from odoo.exceptions import UserError, ValidationError


class TelebirrOrderService(models.AbstractModel):
    """
    Telebirr Order Service
    
    This service handles payment order creation and checkout URL generation:
    - Creates payment orders with Telebirr API
    - Generates checkout URLs for customers
    - Handles order request signing
    - Manages order status tracking
    """
    _name = 'telebirr.order.service'
    _description = 'Telebirr Order Service'
    
    @api.model
    def create_payment_order(self, config, title, amount, notify_url=None, 
                          merch_order_id=None, customer_phone=None):
        """
        Create payment order with Telebirr
        
        This method implements the complete order creation flow:
        1. Get fabric token for authentication
        2. Build order request with all required fields
        3. Sign request with RSA private key
        4. Send to Telebirr API
        5. Validate response and return result
        
        Args:
            config: telebirr.config record
            title: Payment title/description
            amount: Payment amount (float)
            notify_url: Webhook URL (optional, uses config default)
            merch_order_id: Merchant order ID (optional, auto-generated)
            customer_phone: Customer phone (optional)
            
        Returns:
            Dictionary with order creation response
            
        Raises:
            UserError: If order creation fails
        """
        try:
            # Validate inputs
            self._validate_order_inputs(config, title, amount)
            
            # Get fabric token
            token_service = self.env['telebirr.fabric.token.service']
            fabric_token = token_service.get_cached_token(config)
            
            # Build order request
            request_data = self._build_order_request(
                config, title, amount, notify_url, merch_order_id
            )
            
            # Sign request
            signature_service = self.env['telebirr.signature.service']
            signature = signature_service.sign_request(
                request_data, config.private_key
            )
            request_data['sign'] = signature
            request_data['sign_type'] = 'SHA256WithRSA'
            
            # Log request (debug mode)
            self._log_order_request(config, request_data)
            
            # Make API call
            response = self._send_order_request(config, fabric_token, request_data)
            
            # Validate response
            if response.get('result') != 'SUCCESS':
                error_msg = response.get('msg', 'Unknown error from Telebirr')
                raise UserError(_("Payment order creation failed: %s") % error_msg)
            
            # Log successful response
            self._log_order_response(config, response, success=True)
            
            return response
            
        except Exception as e:
            # Log error
            self._log_order_error(config, str(e), 'order_creation')
            raise UserError(_("Failed to create payment order: %s") % str(e))
    
    @api.model
    def _validate_order_inputs(self, config, title, amount):
        """
        Validate inputs for order creation
        
        Args:
            config: telebirr.config record
            title: Payment title
            amount: Payment amount
            
        Raises:
            ValidationError: If inputs are invalid
        """
        if not config or not config.active:
            raise ValidationError(_("Active Telebirr configuration is required"))
        
        if not title or len(title.strip()) == 0:
            raise ValidationError(_("Payment title is required"))
        
        if not amount or amount <= 0:
            raise ValidationError(_("Payment amount must be greater than 0"))
        
        # Check amount limits (Telebirr typical limits)
        if amount > 100000:  # 100,000 ETB limit
            raise ValidationError(_("Payment amount exceeds maximum limit of 100,000 ETB"))
        
        # Validate configuration fields
        required_fields = ['fabric_app_id', 'app_secret', 'merchant_app_id', 'merchant_code', 'private_key']
        for field in required_fields:
            if not getattr(config, field):
                raise ValidationError(_("Telebirr configuration missing: %s") % field)
    
    @api.model
    def _build_order_request(self, config, title, amount, notify_url, merch_order_id):
        """
        Build payment order request data
        
        Args:
            config: telebirr.config record
            title: Payment title
            amount: Payment amount
            notify_url: Webhook notification URL
            merch_order_id: Merchant order ID
            
        Returns:
            Dictionary with complete order request
        """
        if not notify_url:
            notify_url = config.webhook_url
        
        if not merch_order_id:
            merch_order_id = f"TB{int(time.time() * 1000)}"
        
        # Build biz_content according to Telebirr requirements
        biz_content = {
            "notify_url": notify_url,
            "appid": config.merchant_app_id,
            "merch_code": config.merchant_code,
            "merch_order_id": merch_order_id,
            "trade_type": "Checkout",  # Web checkout
            "title": title,
            "total_amount": str(amount),
            "trans_currency": "ETB",
            "timeout_express": f"{config.timeout_express}m",
            "business_type": "BuyGoods",
            "payee_identifier": config.merchant_code,
            "payee_identifier_type": "04",
            "payee_type": "5000"
        }
        
        # Add redirect URL if configured
        if config.redirect_url:
            biz_content["redirect_url"] = config.redirect_url
        
        # Build complete request
        request_data = {
            "timestamp": str(int(time.time())),
            "nonce_str": str(uuid.uuid4()).replace('-', ''),
            "method": "payment.preorder",
            "version": "1.0",
            "biz_content": biz_content
        }
        
        return request_data
    
    @api.model
    def _send_order_request(self, config, fabric_token, request_data):
        """
        Send order request to Telebirr API
        
        Args:
            config: telebirr.config record
            fabric_token: Fabric token for authentication
            request_data: Signed order request data
            
        Returns:
            Response dictionary from Telebirr API
            
        Raises:
            Exception: If API call fails
        """
        url = f"{config.base_url}/payment/v1/merchant/preOrder"
        
        headers = {
            "Content-Type": "application/json",
            "X-APP-Key": config.fabric_app_id,
            "Authorization": fabric_token
        }
        
        # Make API call
        import requests
        response = requests.post(
            url,
            headers=headers,
            data=json.dumps(request_data),
            verify=False,  # For development only - use proper SSL in production
            timeout=30
        )
        
        # Check HTTP status
        response.raise_for_status()
        
        # Parse and return response
        return response.json()
    
    @api.model
    def generate_checkout_url(self, config, prepay_id):
        """
        Generate complete checkout URL for payment
        
        This method:
        1. Builds checkout parameters
        2. Signs parameters with RSA
        3. Creates raw request string
        4. Combines with Telebirr web URL
        
        Args:
            config: telebirr.config record
            prepay_id: Prepay ID from order creation
            
        Returns:
            Complete checkout URL string
            
        Raises:
            UserError: If URL generation fails
        """
        try:
            # Build checkout parameters
            params = {
                "appid": config.merchant_app_id,
                "merch_code": config.merchant_code,
                "nonce_str": str(uuid.uuid4()).replace('-', ''),
                "prepay_id": prepay_id,
                "timestamp": str(int(time.time()))
            }
            
            # Sign parameters
            signature_service = self.env['telebirr.signature.service']
            signature = signature_service.sign_request(params, config.private_key)
            
            # Build raw request string
            raw_params = [
                f"appid={params['appid']}",
                f"merch_code={params['merch_code']}",
                f"nonce_str={params['nonce_str']}",
                f"prepay_id={params['prepay_id']}",
                f"timestamp={params['timestamp']}",
                f"sign={signature}",
                "sign_type=SHA256WithRSA"
            ]
            
            raw_request = "&".join(raw_params)
            
            # Complete checkout URL
            checkout_url = f"{config.web_url}{raw_request}&version=1.0&trade_type=Checkout"
            
            # Log checkout URL generation
            self._log_checkout_url_generation(config, prepay_id, checkout_url)
            
            return checkout_url
            
        except Exception as e:
            self._log_order_error(config, f"Checkout URL generation failed: {str(e)}", 'url_generation')
            raise UserError(_("Failed to generate checkout URL: %s") % str(e))
    
    @api.model
    def _log_order_request(self, config, request_data):
        """
        Log order request for debugging
        
        Args:
            config: telebirr.config record
            request_data: Order request data
        """
        if not self.env.context.get('telebirr_debug', False):
            return
        
        # Remove sensitive data for logging
        log_data = {
            'config_id': config.id,
            'request_data': {
                'method': request_data.get('method'),
                'version': request_data.get('version'),
                'timestamp': request_data.get('timestamp'),
                'nonce_str': request_data.get('nonce_str'),
                'has_biz_content': bool(request_data.get('biz_content')),
                'sign_type': request_data.get('sign_type'),
                'has_sign': bool(request_data.get('sign'))
            },
            'timestamp': datetime.now().isoformat(),
            'action': 'order_request'
        }
        
        self._write_to_log(log_data)
    
    @api.model
    def _log_order_response(self, config, response_data, success=True):
        """
        Log order response
        
        Args:
            config: telebirr.config record
            response_data: Response from Telebirr
            success: Whether response was successful
        """
        if not self.env.context.get('telebirr_debug', False):
            return
        
        log_data = {
            'config_id': config.id,
            'response_data': {
                'result': response_data.get('result'),
                'code': response_data.get('code'),
                'msg': response_data.get('msg'),
                'has_biz_content': bool(response_data.get('biz_content')),
                'has_nonce_str': bool(response_data.get('nonce_str')),
                'has_sign': bool(response_data.get('sign'))
            },
            'timestamp': datetime.now().isoformat(),
            'action': 'order_response',
            'success': success
        }
        
        self._write_to_log(log_data)
    
    @api.model
    def _log_checkout_url_generation(self, config, prepay_id, checkout_url):
        """
        Log checkout URL generation
        
        Args:
            config: telebirr.config record
            prepay_id: Prepay ID
            checkout_url: Generated checkout URL
        """
        if not self.env.context.get('telebirr_debug', False):
            return
        
        log_data = {
            'config_id': config.id,
            'prepay_id': prepay_id,
            'checkout_url_length': len(checkout_url),
            'timestamp': datetime.now().isoformat(),
            'action': 'checkout_url_generation'
        }
        
        self._write_to_log(log_data)
    
    @api.model
    def _log_order_error(self, config, error_message, error_type):
        """
        Log order-related errors
        
        Args:
            config: telebirr.config record
            error_message: Error description
            error_type: Type of error
        """
        log_data = {
            'config_id': config.id,
            'error_message': error_message,
            'error_type': error_type,
            'timestamp': datetime.now().isoformat(),
            'action': 'order_error'
        }
        
        self._write_to_log(log_data)
    
    @api.model
    def _write_to_log(self, log_data):
        """
        Write log entry
        
        Args:
            log_data: Dictionary of log data
        """
        try:
            # Create log record if model exists
            if hasattr(self.env, 'telebirr.log') and self.env['telebirr.log']._auto_init:
                self.env['telebirr.log'].sudo().create(log_data)
            else:
                # Fallback to system logger
                from odoo.tools import logging
                logger = logging.getLogger('telebirr_order')
                logger.info(f"Order Service Log: {json.dumps(log_data)}")
        except Exception:
            # Silent fail for logging to avoid breaking main flow
            pass
    
    @api.model
    def validate_checkout_url(self, checkout_url):
        """
        Validate generated checkout URL format
        
        Args:
            checkout_url: Generated checkout URL
            
        Returns:
            Tuple (is_valid, error_message)
        """
        if not checkout_url:
            return False, "Checkout URL cannot be empty"
        
        # Check if it starts with correct base URL
        valid_prefixes = [
            'https://developerportal.ethiotelebirr.et:38443/payment/web/paygate?',
            'https://telebirrappcube.ethiomobilemoney.et:38443/payment/web/paygate?'
        ]
        
        if not any(checkout_url.startswith(prefix) for prefix in valid_prefixes):
            return False, "Invalid checkout URL base"
        
        # Check for required parameters
        required_params = ['appid=', 'merch_code=', 'nonce_str=', 'prepay_id=', 
                          'timestamp=', 'sign=', 'sign_type=']
        
        for param in required_params:
            if param not in checkout_url:
                return False, f"Missing required parameter: {param}"
        
        # Check for version and trade_type
        if 'version=1.0' not in checkout_url:
            return False, "Missing version parameter"
        
        if 'trade_type=Checkout' not in checkout_url:
            return False, "Missing trade_type parameter"
        
        return True, ""
    
    @api.model
    def get_order_status_info(self, config, merch_order_id):
        """
        Get comprehensive order status information
        
        Args:
            config: telebirr.config record
            merch_order_id: Merchant order ID
            
        Returns:
            Dictionary with status information
        """
        try:
            # Query order status
            from ..services.query_service import TelebirrQueryService
            query_service = TelebirrQueryService(self.env)
            result = query_service.query_order(config, merch_order_id)
            
            if result.get('result') != 'SUCCESS':
                return {
                    'status': 'error',
                    'message': result.get('msg', 'Failed to get order status'),
                    'details': result
                }
            
            biz_content = result.get('biz_content', {})
            
            return {
                'status': 'success',
                'order_status': biz_content.get('trade_status'),
                'payment_order_id': biz_content.get('payment_order_id'),
                'trans_time': biz_content.get('trans_time'),
                'trans_amount': biz_content.get('total_amount'),
                'trans_currency': biz_content.get('trans_currency'),
                'details': result
            }
            
        except Exception as e:
            return {
                'status': 'error',
                'message': str(e),
                'details': None
            }
