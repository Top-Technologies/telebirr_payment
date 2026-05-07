# -*- coding: utf-8 -*-

import json
from datetime import datetime

from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError


class TelebirrPaymentWizard(models.TransientModel):
    """
    Telebirr Payment Wizard
    
    This wizard provides the user interface for initiating Telebirr payments
    from Sales Orders and Invoices. It handles:
    - Payment amount validation
    - Customer information
    - Payment initiation
    - Checkout URL generation
    - Transaction tracking
    """
    _name = 'telebirr.payment.wizard'
    _description = 'Telebirr Payment Wizard'
    
    # Source Document Information
    res_id = fields.Integer('Document ID', readonly=True,
                           help='ID of the source document (Sales Order/Invoice)')
    res_model = fields.Char('Document Model', readonly=True,
                          help='Model of the source document')
    res_name = fields.Char('Document Name', readonly=True,
                          help='Name of the source document')
    
    # Payment Details
    amount = fields.Float('Payment Amount', required=True, digits=(16, 2),
                        help='Amount to pay via Telebirr')
    currency_id = fields.Many2one('res.currency', 'Currency', required=True,
                                 help='Payment currency (should be ETB)')
    partner_id = fields.Many2one('res.partner', 'Customer', required=True,
                                 help='Customer making the payment')
    
    # Telebirr Specific Fields
    customer_phone = fields.Char('Customer Phone', size=20,
                                help='Customer phone number (optional, auto-filled from partner)')
    payment_title = fields.Char('Payment Title', required=True, size=512,
                              help='Description of payment for customer')
    
    # Configuration
    config_id = fields.Many2one('telebirr.config', 'Telebirr Configuration',
                               required=True,
                               help='Telebirr configuration to use for payment')
    
    # Results and Status
    checkout_url = fields.Char('Checkout URL', readonly=True, size=2048,
                              help='URL for customer to complete payment')
    transaction_id = fields.Many2one('telebirr.transaction', 'Transaction',
                                   readonly=True, help='Created payment transaction')
    state = fields.Selection([
        ('draft', 'Draft'),
        ('processed', 'Processed'),
        ('error', 'Error')
    ], string='Status', default='draft', readonly=True,
       help='Current state of the payment wizard')
    
    error_message = fields.Text('Error Message', readonly=True,
                               help='Error details if payment processing failed')
    
    # Additional Information
    notes = fields.Text('Notes', help='Additional notes about this payment')
    
    @api.model
    def default_get(self, fields_list):
        """
        Set default values from source document
        
        This method automatically populates the wizard with data from the
        source Sales Order or Invoice.
        """
        res = super().default_get(fields_list)
        
        # Get active record from context
        active_model = self.env.context.get('active_model')
        active_id = self.env.context.get('active_id')
        
        if not active_model or not active_id:
            return res
        
        # Set source document information
        res.update({
            'res_model': active_model,
            'res_id': active_id,
        })
        
        # Get document details based on model
        if active_model == 'sale.order':
            order = self.env[active_model].browse(active_id)
            res.update({
                'res_name': order.name,
                'partner_id': order.partner_id.id,
                'currency_id': order.currency_id.id,
                'amount': order.amount_total,
                'payment_title': f'Payment for Order {order.name}',
            })
            
            # Auto-fill customer phone
            if order.partner_id.phone:
                res['customer_phone'] = order.partner_id.phone
                
        elif active_model == 'account.move':
            invoice = self.env[active_model].browse(active_id)
            res.update({
                'res_name': invoice.name,
                'partner_id': invoice.partner_id.id,
                'currency_id': invoice.currency_id.id,
                'amount': invoice.amount_residual,
                'payment_title': f'Payment for Invoice {invoice.name}',
            })
            
            # Auto-fill customer phone
            if invoice.partner_id.phone:
                res['customer_phone'] = invoice.partner_id.phone
        
        # Set default Telebirr configuration
        config = self.env['telebirr.config'].get_default_config()
        if config:
            res['config_id'] = config.id
        
        return res
    
    @api.constrains('amount')
    def _check_amount(self):
        """
        Validate payment amount
        """
        for wizard in self:
            if wizard.amount <= 0:
                raise ValidationError(_('Payment amount must be greater than 0'))
            
            # Check against outstanding amount
            if wizard.res_model and wizard.res_id:
                if wizard.res_model == 'sale.order':
                    order = self.env[wizard.res_model].browse(wizard.res_id)
                    if wizard.amount > order.amount_total:
                        raise ValidationError(_(
                            'Payment amount cannot exceed order total (%.2f)'
                        ) % order.amount_total)
                        
                elif wizard.res_model == 'account.move':
                    invoice = self.env[wizard.res_model].browse(wizard.res_id)
                    if wizard.amount > invoice.amount_residual:
                        raise ValidationError(_(
                            'Payment amount cannot exceed outstanding amount (%.2f)'
                        ) % invoice.amount_residual)
    
    @api.constrains('currency_id')
    def _check_currency(self):
        """
        Validate currency (must be ETB for Telebirr)
        """
        for wizard in self:
            if wizard.currency_id and wizard.currency_id.name != 'ETB':
                raise ValidationError(_('Telebirr only supports Ethiopian Birr (ETB) payments'))
    
    @api.onchange('partner_id')
    def _onchange_partner_id(self):
        """
        Auto-fill customer phone when partner changes
        """
        if self.partner_id and self.partner_id.phone and not self.customer_phone:
            self.customer_phone = self.partner_id.phone
    
    def action_initiate_payment(self):
        """
        Initiate Telebirr payment process
        
        This method:
        1. Validates payment data
        2. Creates transaction record
        3. Calls Telebirr API to create payment order
        4. Generates checkout URL
        5. Updates wizard state
        
        Returns:
            Action to display updated wizard
        """
        self.ensure_one()
        
        try:
            # Validate configuration
            if not self.config_id or not self.config_id.active:
                raise UserError(_('Active Telebirr configuration is required'))
            
            # Create transaction record
            transaction_vals = self._prepare_transaction_vals()
            transaction = self.env['telebirr.transaction'].create(transaction_vals)
            
            # Create payment order with Telebirr
            order_service = self.env['telebirr.order.service']
            order_result = order_service.create_payment_order(
                self.config_id,
                self.payment_title,
                self.amount,
                merch_order_id=transaction.merch_order_id,
                customer_phone=self.customer_phone
            )
            
            # Generate checkout URL
            prepay_id = order_result['biz_content']['prepay_id']
            checkout_url = order_service.generate_checkout_url(
                self.config_id,
                prepay_id
            )
            
            # Update transaction with result
            transaction.write({
                'prepay_id': prepay_id,
                'checkout_url': checkout_url,
                'raw_request': json.dumps(order_result.get('request_data', {})),
                'raw_response': json.dumps(order_result)
            })
            
            # Update wizard state
            self.write({
                'transaction_id': transaction.id,
                'checkout_url': checkout_url,
                'state': 'processed'
            })
            
            # Log successful payment initiation
            self._log_payment_initiation(transaction, 'success')
            
            return {
                'type': 'ir.actions.act_window',
                'name': _('Telebirr Payment'),
                'res_model': 'telebirr.payment.wizard',
                'res_id': self.id,
                'view_mode': 'form',
                'target': 'new',
                'context': dict(self.env.context)
            }
            
        except Exception as e:
            # Update wizard with error
            self.write({
                'state': 'error',
                'error_message': str(e)
            })
            
            # Log failed payment initiation
            self._log_payment_initiation(None, 'error', str(e))
            
            raise UserError(_('Payment initiation failed: %s') % str(e))
    
    def _prepare_transaction_vals(self):
        """
        Prepare transaction record values
        
        Returns:
            Dictionary with transaction field values
        """
        vals = {
            'amount': self.amount,
            'currency': self.currency_id.name,
            'title': self.payment_title,
            'partner_id': self.partner_id.id,
            'customer_phone': self.customer_phone,
            'config_id': self.config_id.id,
            'status': 'WAIT_PAY',
            'company_id': self.env.company.id
        }
        
        # Link to source document
        if self.res_model == 'sale.order':
            vals['sales_order_id'] = self.res_id
        elif self.res_model == 'account.move':
            vals['invoice_id'] = self.res_id
        
        # Add notes if provided
        if self.notes:
            vals['notes'] = self.notes
        
        return vals
    
    def action_open_checkout(self):
        """
        Open Telebirr checkout page in new tab
        
        Returns:
            Action to open checkout URL
        """
        self.ensure_one()
        
        if not self.checkout_url:
            raise UserError(_('Checkout URL not available'))
        
        return {
            'type': 'ir.actions.act_url',
            'url': self.checkout_url,
            'target': 'new',
        }
    
    def action_view_transaction(self):
        """
        View payment transaction details
        
        Returns:
            Action to display transaction form
        """
        self.ensure_one()
        
        if not self.transaction_id:
            raise UserError(_('No transaction record found'))
        
        return {
            'type': 'ir.actions.act_window',
            'name': _('Payment Transaction'),
            'res_model': 'telebirr.transaction',
            'res_id': self.transaction_id.id,
            'view_mode': 'form',
            'target': 'current',
        }
    
    def action_check_payment_status(self):
        """
        Check payment status from Telebirr
        
        Returns:
            Client notification with status update
        """
        self.ensure_one()
        
        if not self.transaction_id:
            raise UserError(_('No transaction record found'))
        
        try:
            # Check payment status
            result = self.transaction_id.action_check_status()
            
            # Refresh wizard state
            self._compute_state()
            
            return result
            
        except Exception as e:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Status Check Failed'),
                    'message': _('Failed to check payment status: %s') % str(e),
                    'type': 'danger',
                    'sticky': True,
                }
            }
    
    def action_retry_payment(self):
        """
        Retry payment with new transaction
        
        Returns:
            Action to create new payment wizard
        """
        self.ensure_one()
        
        if self.state == 'processed' and self.transaction_id.status in ['PAY_SUCCESS', 'PAYING']:
            raise UserError(_('Cannot retry successful or in-progress payment'))
        
        # Create new wizard with same data
        return {
            'type': 'ir.actions.act_window',
            'name': _('Telebirr Payment'),
            'res_model': 'telebirr.payment.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_res_model': self.res_model,
                'default_res_id': self.res_id,
                'default_amount': self.amount,
                'default_config_id': self.config_id.id,
                'default_partner_id': self.partner_id.id,
                'default_payment_title': self.payment_title,
                'default_customer_phone': self.customer_phone,
                'default_notes': self.notes,
            }
        }
    
    def action_copy_payment_link(self):
        """
        Copy payment link to clipboard
        
        Returns:
            Client notification
        """
        self.ensure_one()
        
        if not self.checkout_url:
            raise UserError(_('Checkout URL not available'))
        
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Payment Link Copied'),
                'message': _('Payment link copied to clipboard'),
                'type': 'success',
                'sticky': False,
            }
        }
    
    @api.depends('transaction_id.status')
    def _compute_state(self):
        """
        Compute wizard state based on transaction status
        """
        for wizard in self:
            if wizard.transaction_id:
                if wizard.transaction_id.status == 'PAY_SUCCESS':
                    wizard.state = 'processed'
                elif wizard.transaction_id.status in ['PAY_FAILED', 'ORDER_CLOSED']:
                    wizard.state = 'error'
                    wizard.error_message = f"Payment {wizard.transaction_id.status}"
    
    def _log_payment_initiation(self, transaction, status, error_message=None):
        """
        Log payment initiation for audit trail
        
        Args:
            transaction: Created transaction record (or None if failed)
            status: 'success' or 'error'
            error_message: Error message if status is 'error'
        """
        try:
            log_data = {
                'user_id': self.env.user.id,
                'res_model': self.res_model,
                'res_id': self.res_id,
                'amount': self.amount,
                'partner_id': self.partner_id.id,
                'config_id': self.config_id.id,
                'status': status,
                'timestamp': datetime.now().isoformat(),
            }
            
            if transaction:
                log_data['transaction_id'] = transaction.id
                log_data['merch_order_id'] = transaction.merch_order_id
            
            if error_message:
                log_data['error_message'] = error_message
            
            # Create log record if model exists
            if hasattr(self.env, 'telebirr.log') and self.env['telebirr.log']._auto_init:
                self.env['telebirr.log'].sudo().create(log_data)
            else:
                # Fallback to system logger
                from odoo.tools import logging
                logger = logging.getLogger('telebirr_wizard')
                logger.info(f"Payment Wizard Log: {json.dumps(log_data)}")
                
        except Exception:
            # Silent fail for logging to avoid breaking main flow
            pass
    
    def get_payment_summary(self):
        """
        Get payment summary for display
        
        Returns:
            Dictionary with payment summary information
        """
        self.ensure_one()
        
        summary = {
            'document_name': self.res_name,
            'customer_name': self.partner_id.name,
            'amount': self.amount,
            'currency': self.currency_id.name,
            'payment_title': self.payment_title,
            'customer_phone': self.customer_phone,
            'config_name': self.config_id.name,
            'environment': self.config_id.environment,
        }
        
        if self.transaction_id:
            summary.update({
                'transaction_id': self.transaction_id.merch_order_id,
                'transaction_status': self.transaction_id.status,
                'checkout_url': self.checkout_url,
                'payment_time': self.transaction_id.payment_time,
                'expiration_time': self.transaction_id.expiration_time,
            })
        
        return summary
    
    @api.model
    def get_payment_statistics(self, days=30):
        """
        Get payment statistics for reporting
        
        Args:
            days: Number of days to look back
            
        Returns:
            Dictionary with payment statistics
        """
        # This would require proper logging model
        # For now, return placeholder data
        return {
            'total_payments': 0,
            'successful_payments': 0,
            'failed_payments': 0,
            'total_amount': 0,
            'average_amount': 0,
            'period_days': days
        }
