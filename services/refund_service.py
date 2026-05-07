# -*- coding: utf-8 -*-

import json
import uuid
import time
from datetime import datetime

from odoo import models, api, _
from odoo.exceptions import UserError, ValidationError


class TelebirrRefundService(models.AbstractModel):
    """
    Telebirr Refund Service
    
    This service handles payment refunds through Telebirr API:
    - Process refund requests
    - Track refund status
    - Handle partial and full refunds
    - Create refund records
    - Update transaction status
    """
    _name = 'telebirr.refund.service'
    _description = 'Telebirr Refund Service'
    
    # Refund status mapping
    _REFUND_STATUS_MAPPING = {
        'SUCCESS': 'REFUNDED',
        'FAILED': 'REFUND_FAILED',
        'PROCESSING': 'REFUNDING',
        'PENDING': 'REFUNDING'
    }
    
    @api.model
    def process_refund(self, config, merch_order_id, refund_amount, refund_reason=None):
        """
        Process refund request with Telebirr API
        
        This method:
        1. Validate refund request
        2. Get fabric token for authentication
        3. Build refund request with all required fields
        4. Sign request with RSA private key
        5. Send to Telebirr refund API
        6. Create refund record and track status
        
        Args:
            config: telebirr.config record
            merch_order_id: Original merchant order ID to refund
            refund_amount: Amount to refund
            refund_reason: Reason for refund (optional)
            
        Returns:
            Dictionary with refund processing result
            
        Raises:
            UserError: If refund processing fails
        """
        try:
            # Validate refund request
            self._validate_refund_request(config, merch_order_id, refund_amount)
            
            # Get fabric token
            token_service = self.env['telebirr.fabric.token.service']
            fabric_token = token_service.get_cached_token(config)
            
            # Build refund request
            request_data = self._build_refund_request(
                config, merch_order_id, refund_amount, refund_reason
            )
            
            # Sign request
            signature_service = self.env['telebirr.signature.service']
            signature = signature_service.sign_request(
                request_data, config.private_key
            )
            request_data['sign'] = signature
            request_data['sign_type'] = 'SHA256WithRSA'
            
            # Log refund request
            self._log_refund_request(config, merch_order_id, refund_amount, request_data)
            
            # Make API call
            response = self._send_refund_request(config, fabric_token, request_data)
            
            # Validate response
            if response.get('result') != 'SUCCESS':
                error_msg = response.get('msg', 'Unknown error from Telebirr')
                raise UserError(_("Refund request failed: %s") % error_msg)
            
            # Create refund record
            refund_record = self._create_refund_record(
                config, merch_order_id, refund_amount, refund_reason, response
            )
            
            # Log successful response
            self._log_refund_response(config, merch_order_id, response, success=True)
            
            return {
                'status': 'success',
                'refund_id': refund_record.id,
                'refund_order_id': response.get('biz_content', {}).get('refund_order_id'),
                'message': 'Refund request submitted successfully'
            }
            
        except Exception as e:
            # Log error
            self._log_refund_error(config, merch_order_id, str(e), 'refund_failed')
            raise UserError(_("Failed to process refund: %s") % str(e))
    
    @api.model
    def _validate_refund_request(self, config, merch_order_id, refund_amount):
        """
        Validate refund request parameters
        
        Args:
            config: telebirr.config record
            merch_order_id: Original merchant order ID
            refund_amount: Amount to refund
            
        Raises:
            ValidationError: If validation fails
        """
        if not config or not config.active:
            raise ValidationError(_("Active Telebirr configuration is required"))
        
        if not merch_order_id or len(merch_order_id.strip()) == 0:
            raise ValidationError(_("Merchant order ID is required"))
        
        if not refund_amount or refund_amount <= 0:
            raise ValidationError(_("Refund amount must be greater than 0"))
        
        # Find original transaction
        original_transaction = self.env['telebirr.transaction'].search([
            ('merch_order_id', '=', merch_order_id),
            ('status', '=', 'PAY_SUCCESS')
        ], limit=1)
        
        if not original_transaction:
            raise ValidationError(_("Original successful transaction not found"))
        
        # Check refund amount against original amount
        total_refunded = self._get_total_refunded_amount(merch_order_id)
        remaining_refundable = original_transaction.amount - total_refunded
        
        if refund_amount > remaining_refundable:
            raise ValidationError(_(
                "Refund amount %.2f exceeds remaining refundable amount %.2f"
            ) % (refund_amount, remaining_refundable))
        
        # Check if refund is within time limits (e.g., 30 days)
        if original_transaction.payment_time:
            refund_deadline = original_transaction.payment_time + timedelta(days=30)
            if datetime.now() > refund_deadline:
                raise ValidationError(_("Refund period expired (30 days limit)"))
    
    @api.model
    def _get_total_refunded_amount(self, merch_order_id):
        """
        Get total amount already refunded for a transaction
        
        Args:
            merch_order_id: Merchant order ID
            
        Returns:
            Total refunded amount
        """
        # This would require a refund transaction model
        # For now, return 0 as placeholder
        return 0.0
    
    @api.model
    def _build_refund_request(self, config, merch_order_id, refund_amount, refund_reason):
        """
        Build refund request data
        
        Args:
            config: telebirr.config record
            merch_order_id: Original merchant order ID
            refund_amount: Amount to refund
            refund_reason: Reason for refund
            
        Returns:
            Dictionary with complete refund request
        """
        # Generate unique refund order ID
        refund_order_id = f"RF{int(time.time() * 1000)}"
        
        request_data = {
            "timestamp": str(int(time.time())),
            "nonce_str": str(uuid.uuid4()).replace('-', ''),
            "method": "payment.refund",
            "version": "1.0",
            "biz_content": {
                "appid": config.merchant_app_id,
                "merch_code": config.merchant_code,
                "merch_order_id": merch_order_id,
                "refund_order_id": refund_order_id,
                "total_amount": str(refund_amount),
                "trans_currency": "ETB",
                "refund_reason": refund_reason or "Customer refund request"
            }
        }
        
        return request_data
    
    @api.model
    def _send_refund_request(self, config, fabric_token, request_data):
        """
        Send refund request to Telebirr API
        
        Args:
            config: telebirr.config record
            fabric_token: Fabric token for authentication
            request_data: Signed refund request data
            
        Returns:
            Response dictionary from Telebirr API
            
        Raises:
            Exception: If API call fails
        """
        url = f"{config.base_url}/payment/v1/merchant/refund"
        
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
    def _create_refund_record(self, config, merch_order_id, refund_amount, refund_reason, response):
        """
        Create refund transaction record
        
        Args:
            config: telebirr.config record
            merch_order_id: Original merchant order ID
            refund_amount: Refund amount
            refund_reason: Reason for refund
            response: Response from Telebirr API
            
        Returns:
            Created refund transaction record
        """
        # Get original transaction
        original_transaction = self.env['telebirr.transaction'].search([
            ('merch_order_id', '=', merch_order_id)
        ], limit=1)
        
        # Create refund transaction
        refund_vals = {
            'merch_order_id': response.get('biz_content', {}).get('refund_order_id'),
            'amount': -refund_amount,  # Negative amount for refund
            'currency': 'ETB',
            'title': f"Refund for {original_transaction.title}",
            'status': 'REFUNDING',
            'partner_id': original_transaction.partner_id.id,
            'sales_order_id': original_transaction.sales_order_id.id,
            'invoice_id': original_transaction.invoice_id.id,
            'config_id': config.id,
            'raw_request': json.dumps(response.get('request_data', {})),
            'raw_response': json.dumps(response),
            'company_id': config.company_id.id,
            'notes': f"Original order: {merch_order_id}\nReason: {refund_reason or 'Not specified'}"
        }
        
        # This would require a refund transaction model
        # For now, we'll update the original transaction
        original_transaction.write({
            'status': 'REFUNDING',
            'notes': f"Refund in progress: {refund_amount} ETB\nReason: {refund_reason}"
        })
        
        return original_transaction
    
    @api.model
    def query_refund_status(self, config, refund_order_id):
        """
        Query refund status from Telebirr API
        
        Args:
            config: telebirr.config record
            refund_order_id: Refund order ID to query
            
        Returns:
            Dictionary with refund status information
        """
        try:
            # Get fabric token
            token_service = self.env['telebirr.fabric.token.service']
            fabric_token = token_service.get_cached_token(config)
            
            # Build query request
            request_data = {
                "timestamp": str(int(time.time())),
                "nonce_str": str(uuid.uuid4()).replace('-', ''),
                "method": "payment.query",
                "version": "1.0",
                "biz_content": {
                    "appid": config.merchant_app_id,
                    "merch_code": config.merchant_code,
                    "merch_order_id": refund_order_id
                }
            }
            
            # Sign request
            signature_service = self.env['telebirr.signature.service']
            signature = signature_service.sign_request(
                request_data, config.private_key
            )
            request_data['sign'] = signature
            request_data['sign_type'] = 'SHA256WithRSA'
            
            # Make API call
            url = f"{config.base_url}/payment/v1/merchant/queryOrder"
            
            headers = {
                "Content-Type": "application/json",
                "X-APP-Key": config.fabric_app_id,
                "Authorization": fabric_token
            }
            
            response = requests.post(
                url,
                headers=headers,
                data=json.dumps(request_data),
                verify=False,
                timeout=30
            )
            
            response.raise_for_status()
            result = response.json()
            
            if result.get('result') != 'SUCCESS':
                return {
                    'status': 'error',
                    'message': result.get('msg', 'Failed to query refund status')
                }
            
            biz_content = result.get('biz_content', {})
            refund_status = biz_content.get('trade_status')
            
            return {
                'status': 'success',
                'refund_status': refund_status,
                'refund_amount': biz_content.get('total_amount'),
                'refund_time': biz_content.get('trans_time'),
                'details': result
            }
            
        except Exception as e:
            return {
                'status': 'error',
                'message': str(e)
            }
    
    @api.model
    def process_refund_status_update(self, refund_order_id, status_data):
        """
        Process refund status update (from webhook or polling)
        
        Args:
            refund_order_id: Refund order ID
            status_data: Status update data
            
        Returns:
            Tuple (success, message)
        """
        try:
            # Find refund record
            refund_record = self.env['telebirr.transaction'].search([
                ('merch_order_id', '=', refund_order_id)
            ], limit=1)
            
            if not refund_record:
                return False, "Refund record not found"
            
            # Map status
            trade_status = status_data.get('trade_status')
            new_status = self._REFUND_STATUS_MAPPING.get(trade_status, trade_status)
            
            # Update refund record
            update_vals = {
                'status': new_status,
                'raw_response': json.dumps(status_data)
            }
            
            if new_status == 'REFUNDED':
                update_vals['payment_time'] = datetime.now()
                # Create Odoo refund payment
                self._create_refund_payment(refund_record, status_data)
            
            refund_record.write(update_vals)
            
            return True, f"Refund status updated to {new_status}"
            
        except Exception as e:
            return False, f"Failed to update refund status: {str(e)}"
    
    @api.model
    def _create_refund_payment(self, refund_record, status_data):
        """
        Create Odoo refund payment record
        
        Args:
            refund_record: Refund transaction record
            status_data: Status data from Telebirr
        """
        try:
            # Get original payment
            original_payment = refund_record.payment_id
            if not original_payment:
                return False, "Original payment not found"
            
            # Create refund payment
            refund_payment_vals = {
                'payment_type': 'outbound',
                'partner_type': 'customer',
                'partner_id': refund_record.partner_id.id,
                'amount': abs(refund_record.amount),  # Make positive
                'journal_id': original_payment.journal_id.id,
                'company_id': refund_record.company_id.id,
                'currency_id': original_payment.currency_id.id,
                'ref': f'Refund for {original_payment.ref}',
                'date': datetime.now().date(),
                'communication': f'Refund for {refund_record.title}',
                'payment_method_line_ids': [(6, 0, original_payment.payment_method_line_ids.ids)],
            }
            
            refund_payment = self.env['account.payment'].sudo().create(refund_payment_vals)
            refund_payment.action_post()
            
            # Reconcile with original payment
            (original_payment.move_id.line_ids + refund_payment.move_id.line_ids).filtered(
                lambda line: line.account_id.internal_type == 'receivable'
            ).reconcile()
            
            return True, refund_payment.id
            
        except Exception as e:
            return False, f"Failed to create refund payment: {str(e)}"
    
    @api.model
    def _log_refund_request(self, config, merch_order_id, refund_amount, request_data):
        """
        Log refund request for debugging
        
        Args:
            config: telebirr.config record
            merch_order_id: Original merchant order ID
            refund_amount: Refund amount
            request_data: Refund request data
        """
        if not self.env.context.get('telebirr_debug', False):
            return
        
        log_data = {
            'config_id': config.id,
            'merch_order_id': merch_order_id,
            'refund_amount': refund_amount,
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
            'action': 'refund_request'
        }
        
        self._write_to_log(log_data)
    
    @api.model
    def _log_refund_response(self, config, merch_order_id, response_data, success=True):
        """
        Log refund response
        
        Args:
            config: telebirr.config record
            merch_order_id: Original merchant order ID
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
            'action': 'refund_response',
            'success': success
        }
        
        self._write_to_log(log_data)
    
    @api.model
    def _log_refund_error(self, config, merch_order_id, error_message, error_type):
        """
        Log refund-related errors
        
        Args:
            config: telebirr.config record
            merch_order_id: Original merchant order ID
            error_message: Error description
            error_type: Type of error
        """
        log_data = {
            'config_id': config.id,
            'merch_order_id': merch_order_id,
            'error_message': error_message,
            'error_type': error_type,
            'timestamp': datetime.now().isoformat(),
            'action': 'refund_error'
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
                logger = logging.getLogger('telebirr_refund')
                logger.info(f"Refund Service Log: {json.dumps(log_data)}")
        except Exception:
            # Silent fail for logging to avoid breaking main flow
            pass
    
    @api.model
    def get_refund_statistics(self, config, days=30):
        """
        Get refund statistics for monitoring
        
        Args:
            config: telebirr.config record
            days: Number of days to look back
            
        Returns:
            Dictionary with refund statistics
        """
        # This would require a refund transaction model to be implemented
        # For now, return placeholder data
        return {
            'total_refunds': 0,
            'successful_refunds': 0,
            'failed_refunds': 0,
            'total_refund_amount': 0,
            'average_refund_amount': 0,
            'period_days': days
        }
