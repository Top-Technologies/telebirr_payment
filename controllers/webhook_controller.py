# -*- coding: utf-8 -*-

import json
import logging
from datetime import datetime

from odoo import http
from odoo.http import request
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)


class TelebirrWebhookController(http.Controller):
    """
    Telebirr Webhook Controller
    
    This controller handles payment notifications from Telebirr:
    - Receives webhook notifications
    - Verifies RSA signatures
    - Updates transaction status
    - Creates Odoo payment records
    - Handles error scenarios
    """
    
    @http.route('/telebirr/webhook/payment', type='json', auth='public', 
                methods=['POST'], csrf=False)
    def payment_webhook(self):
        """
        Handle payment notifications from Telebirr
        
        Expected webhook payload:
        {
            "appid": "...",
            "merch_order_id": "...",
            "payment_order_id": "...",
            "total_amount": "...",
            "trans_currency": "ETB",
            "trade_status": "Completed|Failure|Pending",
            "trans_end_time": "...",
            "notify_time": "...",
            "sign": "...",
            "sign_type": "SHA256WithRSA"
        }
        
        Returns:
            JSON response with processing status
        """
        try:
            # Get webhook data
            webhook_data = request.jsonrequest
            
            if not webhook_data:
                _logger.error("Empty webhook data received from Telebirr")
                return self._webhook_response('error', 'Empty data received')
            
            _logger.info(f"Telebirr webhook received: {json.dumps(webhook_data)}")
            
            # Validate webhook structure
            validation_result = self._validate_webhook_structure(webhook_data)
            if not validation_result[0]:
                _logger.error(f"Webhook validation failed: {validation_result[1]}")
                return self._webhook_response('error', validation_result[1])
            
            # Find transaction
            transaction = self._find_transaction(webhook_data.get('merch_order_id'))
            if not transaction:
                _logger.warning(f"Transaction not found for merch_order_id: {webhook_data.get('merch_order_id')}")
                return self._webhook_response('error', 'Transaction not found')
            
            # Verify webhook signature
            signature_valid = self._verify_webhook_signature(transaction, webhook_data)
            if not signature_valid:
                _logger.error("Invalid webhook signature from Telebirr")
                return self._webhook_response('error', 'Invalid signature')
            
            # Process payment status update
            processing_result = self._process_payment_status_update(transaction, webhook_data)
            
            if processing_result[0]:
                _logger.info(f"Webhook processed successfully for transaction {transaction.merch_order_id}")
                return self._webhook_response('success', 'Payment status updated')
            else:
                _logger.error(f"Webhook processing failed: {processing_result[1]}")
                return self._webhook_response('error', processing_result[1])
                
        except Exception as e:
            _logger.exception("Unexpected error processing Telebirr webhook")
            return self._webhook_response('error', f'Processing error: {str(e)}')
    
    @http.route('/telebirr/webhook/health', type='http', auth='public', 
                methods=['GET'], csrf=False)
    def webhook_health_check(self):
        """
        Health check endpoint for webhook service
        
        Returns:
            JSON response with service status
        """
        return self._webhook_response('success', 'Webhook service is healthy')
    
    def _validate_webhook_structure(self, webhook_data):
        """
        Validate webhook data structure
        
        Args:
            webhook_data: Dictionary containing webhook payload
            
        Returns:
            Tuple (is_valid, error_message)
        """
        required_fields = [
            'appid', 'merch_order_id', 'payment_order_id', 
            'total_amount', 'trans_currency', 'trade_status', 
            'trans_end_time', 'notify_time', 'sign', 'sign_type'
        ]
        
        # Check required fields
        for field in required_fields:
            if field not in webhook_data:
                return False, f"Missing required field: {field}"
        
        # Validate field values
        if not webhook_data.get('appid'):
            return False, "App ID cannot be empty"
        
        if not webhook_data.get('merch_order_id'):
            return False, "Merchant order ID cannot be empty"
        
        if webhook_data.get('trans_currency') != 'ETB':
            return False, "Only ETB currency is supported"
        
        # Validate trade status
        valid_statuses = ['Completed', 'Failure', 'Pending', 'Paying', 'WAIT_PAY', 'PAY_SUCCESS', 'PAY_FAILED']
        if webhook_data.get('trade_status') not in valid_statuses:
            return False, f"Invalid trade status: {webhook_data.get('trade_status')}"
        
        # Validate signature type
        if webhook_data.get('sign_type') != 'SHA256WithRSA':
            return False, "Invalid signature type"
        
        return True, ""
    
    def _find_transaction(self, merch_order_id):
        """
        Find transaction by merchant order ID
        
        Args:
            merch_order_id: Merchant order ID from webhook
            
        Returns:
            telebirr.transaction record or None
        """
        if not merch_order_id:
            return None
        
        try:
            transaction = request.env['telebirr.transaction'].sudo().search([
                ('merch_order_id', '=', merch_order_id)
            ], limit=1)
            
            return transaction if transaction else None
            
        except Exception as e:
            _logger.error(f"Error finding transaction {merch_order_id}: {str(e)}")
            return None
    
    def _verify_webhook_signature(self, transaction, webhook_data):
        """
        Verify webhook signature using RSA
        
        Args:
            transaction: telebirr.transaction record
            webhook_data: Dictionary containing webhook data
            
        Returns:
            Boolean indicating signature validity
        """
        try:
            config = transaction.config_id
            if not config:
                _logger.error("No configuration found for transaction")
                return False
            
            # Get signature service
            signature_service = request.env['telebirr.signature.service']
            
            # Verify signature
            signature = webhook_data.get('sign')
            public_key = config.public_key or config.private_key  # Use private key if public not available
            
            is_valid = signature_service.verify_signature(
                webhook_data,
                signature,
                public_key
            )
            
            _logger.info(f"Webhook signature verification result: {is_valid}")
            
            return is_valid
            
        except Exception as e:
            _logger.error(f"Signature verification error: {str(e)}")
            return False
    
    def _process_payment_status_update(self, transaction, webhook_data):
        """
        Process payment status update from webhook
        
        Args:
            transaction: telebirr.transaction record
            webhook_data: Dictionary containing webhook data
            
        Returns:
            Tuple (success, message)
        """
        try:
            # Map Telebirr status to internal status
            status_mapping = {
                'Completed': 'PAY_SUCCESS',
                'Failure': 'PAY_FAILED',
                'Pending': 'WAIT_PAY',
                'Paying': 'PAYING',
                'WAIT_PAY': 'WAIT_PAY',
                'PAY_SUCCESS': 'PAY_SUCCESS',
                'PAY_FAILED': 'PAY_FAILED'
            }
            
            trade_status = webhook_data.get('trade_status')
            new_status = status_mapping.get(trade_status, trade_status)
            
            # Check if status actually changed
            if new_status == transaction.status:
                _logger.info(f"Transaction {transaction.merch_order_id} status unchanged: {new_status}")
                return True, "Status unchanged"
            
            # Update transaction
            update_vals = {
                'status': new_status,
                'payment_order_id': webhook_data.get('payment_order_id'),
                'raw_response': json.dumps(webhook_data)
            }
            
            # Set payment time for successful payments
            if new_status == 'PAY_SUCCESS':
                # Parse payment time
                trans_end_time = webhook_data.get('trans_end_time')
                if trans_end_time:
                    try:
                        # Telebirr timestamp format: YYYYMMDDHHMMSS
                        payment_time = datetime.strptime(trans_end_time, "%Y%m%d%H%M%S")
                        update_vals['payment_time'] = payment_time
                    except ValueError:
                        _logger.warning(f"Invalid payment time format: {trans_end_time}")
                        update_vals['payment_time'] = datetime.now()
                else:
                    update_vals['payment_time'] = datetime.now()
                
                # Create Odoo payment record
                payment_result = self._create_odoo_payment(transaction, webhook_data)
                if not payment_result[0]:
                    _logger.error(f"Failed to create Odoo payment: {payment_result[1]}")
                    return payment_result
                
                update_vals['payment_id'] = payment_result[1]
            
            # Update transaction
            transaction.write(update_vals)
            
            # Send notification to user
            self._send_payment_notification(transaction, new_status)
            
            _logger.info(f"Transaction {transaction.merch_order_id} status updated to {new_status}")
            
            return True, f"Status updated to {new_status}"
            
        except Exception as e:
            _logger.error(f"Error processing payment status update: {str(e)}")
            return False, f"Processing error: {str(e)}"
    
    def _create_odoo_payment(self, transaction, webhook_data):
        """
        Create Odoo payment record for successful transaction
        
        Args:
            transaction: telebirr.transaction record
            webhook_data: Dictionary containing webhook data
            
        Returns:
            Tuple (success, payment_id or error_message)
        """
        try:
            # Check if payment already exists
            if transaction.payment_id:
                return True, transaction.payment_id.id
            
            # Get payment journal
            journal = request.env['account.journal'].sudo().search([
                ('type', '=', 'bank'),
                ('company_id', '=', transaction.company_id.id)
            ], limit=1)
            
            if not journal:
                return False, "No bank journal found for payment processing"
            
            # Create payment
            payment_vals = {
                'payment_type': 'inbound',
                'partner_type': 'customer',
                'partner_id': transaction.partner_id.id,
                'amount': transaction.amount,
                'journal_id': journal.id,
                'company_id': transaction.company_id.id,
                'currency_id': request.env.ref('base.ETB').id if request.env.ref('base.ETB') else transaction.company_id.currency_id.id,
                'ref': f'Telebirr Payment - {transaction.merch_order_id}',
                'date': transaction.payment_time.date() if transaction.payment_time else datetime.now().date(),
                'communication': f'Telebirr payment for {transaction.title}',
            }
            
            payment = request.env['account.payment'].sudo().create(payment_vals)
            payment.action_post()
            
            # Reconcile with invoice if applicable
            if transaction.invoice_id and transaction.invoice_id.state == 'posted':
                self._reconcile_payment_with_invoice(payment, transaction.invoice_id)
            
            return True, payment.id
            
        except Exception as e:
            _logger.error(f"Error creating Odoo payment: {str(e)}")
            return False, f"Payment creation error: {str(e)}"
    
    def _reconcile_payment_with_invoice(self, payment, invoice):
        """
        Reconcile payment with invoice
        
        Args:
            payment: account.payment record
            invoice: account.move record (invoice)
        """
        try:
            # Get payment move lines
            payment_lines = payment.move_id.line_ids.filtered(
                lambda line: line.account_id.internal_type == 'receivable'
            )
            
            # Get invoice move lines
            invoice_lines = invoice.line_ids.filtered(
                lambda line: line.account_id.internal_type == 'receivable'
            )
            
            # Reconcile
            (payment_lines + invoice_lines).reconcile()
            
            _logger.info(f"Payment {payment.id} reconciled with invoice {invoice.id}")
            
        except Exception as e:
            _logger.error(f"Error reconciling payment with invoice: {str(e)}")
    
    def _send_payment_notification(self, transaction, status):
        """
        Send payment notification to users
        
        Args:
            transaction: telebirr.transaction record
            status: New payment status
        """
        try:
            if status == 'PAY_SUCCESS':
                # Send success notification
                template = request.env.ref('telebirr_payment.email_payment_success', raise_if_not_found=False)
                if template:
                    template.sudo().send_mail(
                        transaction.id,
                        force_send=True,
                        email_values={
                            'email_to': transaction.partner_id.email,
                            'email_from': request.env.company.email,
                        }
                    )
                
                # Send in-app notification
                request.env['mail.message'].sudo().create({
                    'body': f'Telebirr payment of {transaction.amount} ETB completed successfully!',
                    'model': 'telebirr.transaction',
                    'res_id': transaction.id,
                    'message_type': 'notification',
                })
                
            elif status == 'PAY_FAILED':
                # Send failure notification
                request.env['mail.message'].sudo().create({
                    'body': f'Telebirr payment of {transaction.amount} ETB failed!',
                    'model': 'telebirr.transaction',
                    'res_id': transaction.id,
                    'message_type': 'notification',
                })
                
        except Exception as e:
            _logger.error(f"Error sending payment notification: {str(e)}")
    
    def _webhook_response(self, status, message):
        """
        Create standardized webhook response
        
        Args:
            status: Response status (success/error)
            message: Response message
            
        Returns:
            Dictionary with webhook response
        """
        response = {
            'status': status,
            'message': message,
            'timestamp': datetime.now().isoformat()
        }
        
        # Set HTTP status code
        http_status = 200 if status == 'success' else 400
        
        return request.make_json_response(response, status=http_status)
    
    @http.route('/telebirr/webhook/test', type='http', auth='user', 
                methods=['POST'], csrf=False)
    def test_webhook(self, **kwargs):
        """
        Test webhook endpoint (for development/testing)
        
        Returns:
            JSON response with test data
        """
        try:
            # Get test configuration
            config = request.env['telebirr.config'].sudo().search([
                ('active', '=', True),
                ('company_id', '=', request.env.company.id)
            ], limit=1)
            
            if not config:
                return self._webhook_response('error', 'No Telebirr configuration found')
            
            # Create test webhook data
            test_webhook = {
                'appid': config.merchant_app_id,
                'merch_order_id': 'TEST123456789',
                'payment_order_id': 'TESTPAY123',
                'total_amount': '100.00',
                'trans_currency': 'ETB',
                'trade_status': 'Completed',
                'trans_end_time': datetime.now().strftime('%Y%m%d%H%M%S'),
                'notify_time': datetime.now().strftime('%Y%m%d%H%M%S'),
                'sign': 'TEST_SIGNATURE',
                'sign_type': 'SHA256WithRSA'
            }
            
            # Process test webhook
            result = self.payment_webhook()
            
            return request.make_json_response({
                'status': 'success',
                'message': 'Test webhook processed',
                'test_data': test_webhook,
                'result': result
            })
            
        except Exception as e:
            _logger.exception("Error in test webhook")
            return self._webhook_response('error', f'Test webhook error: {str(e)}')
