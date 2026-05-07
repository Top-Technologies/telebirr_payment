# -*- coding: utf-8 -*-

import json
import uuid
import time
from datetime import datetime

from odoo import models, api, _
from odoo.exceptions import UserError, ValidationError


class TelebirrQueryService(models.AbstractModel):
    """
    Telebirr Query Service
    
    This service handles payment status queries from Telebirr API:
    - Query payment order status
    - Handle manual status checking
    - Support webhook failure recovery
    - Map Telebirr statuses to internal statuses
    """
    _name = 'telebirr.query.service'
    _description = 'Telebirr Query Service'
    
    # Status mapping from Telebirr to internal
    _STATUS_MAPPING = {
        'WAIT_PAY': 'WAIT_PAY',
        'PAYING': 'PAYING',
        'PAY_SUCCESS': 'PAY_SUCCESS',
        'PAY_FAILED': 'PAY_FAILED',
        'ORDER_CLOSED': 'ORDER_CLOSED',
        'PENDING': 'WAIT_PAY',
        'SUCCESS': 'PAY_SUCCESS',
        'FAILURE': 'PAY_FAILED',
        'CLOSED': 'ORDER_CLOSED'
    }
    
    @api.model
    def query_order(self, config, merch_order_id):
        """
        Query payment order status from Telebirr API
        
        This method:
        1. Get fabric token for authentication
        2. Build query request with merchant order ID
        3. Sign request with RSA private key
        4. Send to Telebirr query API
        5. Validate and return response
        
        Args:
            config: telebirr.config record
            merch_order_id: Merchant order ID to query
            
        Returns:
            Dictionary with query response from Telebirr
            
        Raises:
            UserError: If query fails
        """
        try:
            # Validate inputs
            self._validate_query_inputs(config, merch_order_id)
            
            # Get fabric token
            token_service = self.env['telebirr.fabric.token.service']
            fabric_token = token_service.get_cached_token(config)
            
            # Build query request
            request_data = self._build_query_request(config, merch_order_id)
            
            # Sign request
            signature_service = self.env['telebirr.signature.service']
            signature = signature_service.sign_request(
                request_data, config.private_key
            )
            request_data['sign'] = signature
            request_data['sign_type'] = 'SHA256WithRSA'
            
            # Log query request
            self._log_query_request(config, merch_order_id, request_data)
            
            # Make API call
            response = self._send_query_request(config, fabric_token, request_data)
            
            # Validate response
            if response.get('result') != 'SUCCESS':
                error_msg = response.get('msg', 'Unknown error from Telebirr')
                raise UserError(_("Payment query failed: %s") % error_msg)
            
            # Log successful response
            self._log_query_response(config, merch_order_id, response, success=True)
            
            return response
            
        except Exception as e:
            # Log error
            self._log_query_error(config, merch_order_id, str(e), 'query_failed')
            raise UserError(_("Failed to query payment status: %s") % str(e))
    
    @api.model
    def _validate_query_inputs(self, config, merch_order_id):
        """
        Validate inputs for query request
        
        Args:
            config: telebirr.config record
            merch_order_id: Merchant order ID
            
        Raises:
            ValidationError: If inputs are invalid
        """
        if not config or not config.active:
            raise ValidationError(_("Active Telebirr configuration is required"))
        
        if not merch_order_id or len(merch_order_id.strip()) == 0:
            raise ValidationError(_("Merchant order ID is required"))
        
        # Validate merchant order ID format
        if not merch_order_id.startswith('TB'):
            raise ValidationError(_("Invalid merchant order ID format"))
        
        # Validate configuration fields
        required_fields = ['fabric_app_id', 'app_secret', 'merchant_app_id', 'merchant_code', 'private_key']
        for field in required_fields:
            if not getattr(config, field):
                raise ValidationError(_("Telebirr configuration missing: %s") % field)
    
    @api.model
    def _build_query_request(self, config, merch_order_id):
        """
        Build query request data
        
        Args:
            config: telebirr.config record
            merch_order_id: Merchant order ID
            
        Returns:
            Dictionary with complete query request
        """
        request_data = {
            "timestamp": str(int(time.time())),
            "nonce_str": str(uuid.uuid4()).replace('-', ''),
            "method": "payment.query",
            "version": "1.0",
            "biz_content": {
                "appid": config.merchant_app_id,
                "merch_code": config.merchant_code,
                "merch_order_id": merch_order_id
            }
        }
        
        return request_data
    
    @api.model
    def _send_query_request(self, config, fabric_token, request_data):
        """
        Send query request to Telebirr API
        
        Args:
            config: telebirr.config record
            fabric_token: Fabric token for authentication
            request_data: Signed query request data
            
        Returns:
            Response dictionary from Telebirr API
            
        Raises:
            Exception: If API call fails
        """
        url = f"{config.base_url}/payment/v1/merchant/queryOrder"
        
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
    def map_telebirr_status(self, telebirr_status):
        """
        Map Telebirr status to internal status
        
        Args:
            telebirr_status: Status from Telebirr API
            
        Returns:
            Internal status string
        """
        return self._STATUS_MAPPING.get(telebirr_status, telebirr_status)
    
    @api.model
    def get_payment_details(self, config, merch_order_id):
        """
        Get comprehensive payment details
        
        This method queries payment status and extracts detailed information
        for display and processing.
        
        Args:
            config: telebirr.config record
            merch_order_id: Merchant order ID
            
        Returns:
            Dictionary with detailed payment information
        """
        try:
            # Query payment status
            result = self.query_order(config, merch_order_id)
            
            if result.get('result') != 'SUCCESS':
                return {
                    'status': 'error',
                    'message': result.get('msg', 'Failed to get payment details'),
                    'details': result
                }
            
            biz_content = result.get('biz_content', {})
            
            # Extract payment details
            payment_details = {
                'status': 'success',
                'telebirr_status': biz_content.get('trade_status'),
                'internal_status': self.map_telebirr_status(biz_content.get('trade_status')),
                'payment_order_id': biz_content.get('payment_order_id'),
                'trans_time': biz_content.get('trans_time'),
                'trans_amount': biz_content.get('total_amount'),
                'trans_currency': biz_content.get('trans_currency'),
                'buyer_info': biz_content.get('buyer_info'),
                'goods_info': biz_content.get('goods_info'),
                'merch_order_id': merch_order_id,
                'query_time': datetime.now().isoformat(),
                'raw_response': result
            }
            
            # Parse transaction time if available
            trans_time = biz_content.get('trans_time')
            if trans_time:
                try:
                    # Telebirr timestamp format: YYYYMMDDHHMMSS
                    payment_details['formatted_trans_time'] = datetime.strptime(
                        trans_time, "%Y%m%d%H%M%S"
                    ).isoformat()
                except ValueError:
                    payment_details['formatted_trans_time'] = trans_time
            
            return payment_details
            
        except Exception as e:
            return {
                'status': 'error',
                'message': str(e),
                'details': None
            }
    
    @api.model
    def batch_query_orders(self, config, merch_order_ids):
        """
        Query multiple orders in batch
        
        Args:
            config: telebirr.config record
            merch_order_ids: List of merchant order IDs
            
        Returns:
            Dictionary with results for each order ID
        """
        results = {}
        
        for merch_order_id in merch_order_ids:
            try:
                results[merch_order_id] = self.get_payment_details(config, merch_order_id)
            except Exception as e:
                results[merch_order_id] = {
                    'status': 'error',
                    'message': str(e),
                    'details': None
                }
        
        return results
    
    @api.model
    def poll_payment_status(self, config, merch_order_id, max_attempts=10, interval=30):
        """
        Poll payment status until completion or max attempts
        
        This method is useful for real-time status checking
        when immediate confirmation is needed.
        
        Args:
            config: telebirr.config record
            merch_order_id: Merchant order ID
            max_attempts: Maximum number of polling attempts
            interval: Interval between attempts in seconds
            
        Returns:
            Dictionary with final status and polling details
        """
        attempts = 0
        start_time = datetime.now()
        
        while attempts < max_attempts:
            attempts += 1
            
            try:
                # Query status
                details = self.get_payment_details(config, merch_order_id)
                
                # Check if payment is completed
                if details.get('internal_status') in ['PAY_SUCCESS', 'PAY_FAILED', 'ORDER_CLOSED']:
                    return {
                        'status': 'completed',
                        'payment_status': details.get('internal_status'),
                        'attempts': attempts,
                        'duration': str(datetime.now() - start_time),
                        'details': details
                    }
                
                # Wait before next attempt
                if attempts < max_attempts:
                    time.sleep(interval)
                    
            except Exception as e:
                # Log error but continue polling
                self._log_query_error(config, merch_order_id, f"Poll attempt {attempts} failed: {str(e)}", 'poll_error')
                
                if attempts < max_attempts:
                    time.sleep(interval)
        
        # Max attempts reached
        return {
            'status': 'timeout',
            'payment_status': 'unknown',
            'attempts': attempts,
            'duration': str(datetime.now() - start_time),
            'message': f"Payment status not confirmed after {max_attempts} attempts"
        }
    
    @api.model
    def _log_query_request(self, config, merch_order_id, request_data):
        """
        Log query request for debugging
        
        Args:
            config: telebirr.config record
            merch_order_id: Merchant order ID
            request_data: Query request data
        """
        if not self.env.context.get('telebirr_debug', False):
            return
        
        log_data = {
            'config_id': config.id,
            'merch_order_id': merch_order_id,
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
            'action': 'query_request'
        }
        
        self._write_to_log(log_data)
    
    @api.model
    def _log_query_response(self, config, merch_order_id, response_data, success=True):
        """
        Log query response
        
        Args:
            config: telebirr.config record
            merch_order_id: Merchant order ID
            response_data: Response from Telebirr
            success: Whether response was successful
        """
        if not self.env.context.get('telebirr_debug', False):
            return
        
        log_data = {
            'config_id': config.id,
            'merch_order_id': merch_order_id,
            'response_data': {
                'result': response_data.get('result'),
                'code': response_data.get('code'),
                'msg': response_data.get('msg'),
                'has_biz_content': bool(response_data.get('biz_content')),
                'has_nonce_str': bool(response_data.get('nonce_str')),
                'has_sign': bool(response_data.get('sign'))
            },
            'timestamp': datetime.now().isoformat(),
            'action': 'query_response',
            'success': success
        }
        
        self._write_to_log(log_data)
    
    @api.model
    def _log_query_error(self, config, merch_order_id, error_message, error_type):
        """
        Log query-related errors
        
        Args:
            config: telebirr.config record
            merch_order_id: Merchant order ID
            error_message: Error description
            error_type: Type of error
        """
        log_data = {
            'config_id': config.id,
            'merch_order_id': merch_order_id,
            'error_message': error_message,
            'error_type': error_type,
            'timestamp': datetime.now().isoformat(),
            'action': 'query_error'
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
                logger = logging.getLogger('telebirr_query')
                logger.info(f"Query Service Log: {json.dumps(log_data)}")
        except Exception:
            # Silent fail for logging to avoid breaking main flow
            pass
    
    @api.model
    def validate_query_response(self, response_data):
        """
        Validate query response structure
        
        Args:
            response_data: Dictionary from Telebirr API
            
        Returns:
            Tuple (is_valid, error_message)
        """
        if not isinstance(response_data, dict):
            return False, "Invalid response format"
        
        if 'result' not in response_data:
            return False, "Missing result field in response"
        
        if response_data.get('result') != 'SUCCESS':
            error_msg = response_data.get('msg', 'Unknown error')
            return False, f"Query failed: {error_msg}"
        
        if 'biz_content' not in response_data:
            return False, "Missing biz_content in response"
        
        biz_content = response_data['biz_content']
        
        # Check for essential fields in biz_content
        essential_fields = ['trade_status', 'merch_order_id']
        for field in essential_fields:
            if field not in biz_content:
                return False, f"Missing essential field in biz_content: {field}"
        
        return True, ""
    
    @api.model
    def get_query_statistics(self, config, days=7):
        """
        Get query statistics for monitoring
        
        Args:
            config: telebirr.config record
            days: Number of days to look back
            
        Returns:
            Dictionary with query statistics
        """
        # This would require a log model to be implemented
        # For now, return placeholder data
        return {
            'total_queries': 0,
            'successful_queries': 0,
            'failed_queries': 0,
            'average_response_time': 0,
            'most_common_status': 'unknown',
            'period_days': days
        }
