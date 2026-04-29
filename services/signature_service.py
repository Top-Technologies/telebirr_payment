# -*- coding: utf-8 -*-

import base64
import json
from Crypto.Hash import SHA256
from Crypto.Signature import PKCS1_v1_5
from Crypto.PublicKey import RSA
from Crypto import Random

from odoo import models, api, _
from odoo.exceptions import UserError


class TelebirrSignatureService(models.AbstractModel):
    """
    Telebirr RSA Signature Service
    
    This service handles RSA SHA256 signature generation and verification
    for all Telebirr API requests and webhook validation.
    
    Critical Security Component:
    - All API requests must be signed with SHA256withRSA
    - Webhook notifications must be verified with RSA signature
    - Private key signs requests, public key verifies responses
    """
    _name = 'telebirr.signature.service'
    _description = 'Telebirr RSA Signature Service'
    
    # Fields excluded from signature generation
    _EXCLUDE_FIELDS = [
        'sign', 'sign_type', 'header', 'refund_info', 
        'openType', 'raw_request', 'biz_content'
    ]
    
    @api.model
    def sign_request(self, request_data, private_key):
        """
        Sign request data using SHA256withRSA algorithm
        
        This method implements Telebirr's signature requirements:
        1. Exclude specific fields from signature
        2. Include biz_content fields at root level
        3. Sort all parameters alphabetically
        4. Format as key=value&key2=value2
        5. Sign with RSA private key using SHA256
        6. Return Base64 encoded signature
        
        Args:
            request_data: Dictionary containing request parameters
            private_key: RSA private key string in PEM format
            
        Returns:
            Base64 encoded signature string
            
        Raises:
            UserError: If signing fails
        """
        try:
            # Step 1: Extract fields for signature
            sign_data = self._extract_signature_fields(request_data)
            
            # Step 2: Sort keys alphabetically
            sorted_keys = sorted(sign_data.keys())
            
            # Step 3: Create signature string
            sign_string_parts = []
            for key in sorted_keys:
                sign_string_parts.append(f"{key}={sign_data[key]}")
            
            sign_string = "&".join(sign_string_parts)
            
            # Debug: Log signature string (remove in production)
            self._log_signature_process(sign_string, 'signature_string')
            
            # Step 4: Sign with RSA
            signature = self._sign_with_rsa(sign_string, private_key)
            
            return signature
            
        except Exception as e:
            raise UserError(_("RSA signature generation failed: %s") % str(e))
    
    @api.model
    def _extract_signature_fields(self, request_data):
        """
        Extract fields that should participate in signature
        
        According to Telebirr documentation:
        - Exclude: sign, sign_type, header, refund_info, openType, raw_request
        - Include all other root level fields
        - Include biz_content fields at root level (flattened)
        
        Args:
            request_data: Dictionary containing request parameters
            
        Returns:
            Dictionary with fields to sign
        """
        sign_data = {}
        
        # Add root level fields (excluding biz_content)
        for key, value in request_data.items():
            if key not in self._EXCLUDE_FIELDS and key != 'biz_content':
                sign_data[key] = value
        
        # Add biz_content fields at root level
        biz_content = request_data.get('biz_content', {})
        for key, value in biz_content.items():
            if key not in self._EXCLUDE_FIELDS:
                sign_data[key] = value
        
        return sign_data
    
    @api.model
    def _sign_with_rsa(self, data, private_key):
        """
        Sign data string with RSA private key using SHA256
        
        Args:
            data: String to sign
            private_key: RSA private key in PEM format
            
        Returns:
            Base64 encoded signature string
            
        Raises:
            Exception: If signing process fails
        """
        try:
            # Convert PEM key to RSA key object
            # Handle both PEM format and base64 encoded keys
            if '-----BEGIN' in private_key:
                key_bytes = private_key.encode('utf-8')
            else:
                key_bytes = base64.b64decode(private_key)
            
            rsa_key = RSA.importKey(key_bytes)
            
            # Create SHA256 hash of data
            digest = SHA256.new()
            digest.update(data.encode('utf-8'))
            
            # Sign the hash
            signer = PKCS1_v1_5.new(rsa_key)
            signature = signer.sign(digest)
            
            # Return Base64 encoded signature
            return base64.b64encode(signature).decode('utf-8')
            
        except Exception as e:
            raise Exception(f"RSA signing failed: {str(e)}")
    
    @api.model
    def verify_signature(self, request_data, signature, public_key):
        """
        Verify request signature using RSA public key
        
        This method validates webhook notifications from Telebirr:
        1. Recreate signature string from request data
        2. Verify signature using public key
        3. Return True if valid, False otherwise
        
        Args:
            request_data: Dictionary containing webhook data
            signature: Base64 encoded signature from webhook
            public_key: RSA public key string in PEM format
            
        Returns:
            Boolean indicating verification result
        """
        try:
            # Recreate signature string
            sign_string = self._create_webhook_signature_string(request_data)
            
            # Convert public key
            if '-----BEGIN' in public_key:
                key_bytes = public_key.encode('utf-8')
            else:
                key_bytes = base64.b64decode(public_key)
            
            rsa_key = RSA.importKey(key_bytes)
            
            # Create hash of original string
            digest = SHA256.new()
            digest.update(sign_string.encode('utf-8'))
            
            # Verify signature
            verifier = PKCS1_v1_5.new(rsa_key)
            signature_bytes = base64.b64decode(signature)
            
            is_valid = verifier.verify(digest, signature_bytes)
            
            # Log verification result
            self._log_signature_process(sign_string, 'webhook_verification', is_valid)
            
            return is_valid
            
        except Exception:
            # Any exception means verification failed
            self._log_signature_process('Verification failed with exception', 'webhook_error')
            return False
    
    @api.model
    def _create_webhook_signature_string(self, webhook_data):
        """
        Create signature string from webhook data
        
        Webhook signature format is different from API requests:
        - Only include specific webhook fields
        - Sort alphabetically
        - Join with &
        
        Args:
            webhook_data: Dictionary containing webhook notification
            
        Returns:
            String for signature verification
        """
        # Webhook fields to include in signature
        webhook_fields = [
            'appid', 'merch_order_id', 'payment_order_id', 
            'total_amount', 'trans_currency', 'trade_status', 
            'trans_end_time', 'notify_time', 'callback_info'
        ]
        
        sign_data = {}
        for field in webhook_fields:
            if field in webhook_data and webhook_data[field]:
                sign_data[field] = webhook_data[field]
        
        # Sort and join
        sorted_keys = sorted(sign_data.keys())
        sign_string_parts = []
        for key in sorted_keys:
            sign_string_parts.append(f"{key}={sign_data[key]}")
        
        return "&".join(sign_string_parts)
    
    @api.model
    def _log_signature_process(self, data, process_type, result=None):
        """
        Log signature process for debugging
        
        Args:
            data: Data being processed
            process_type: Type of process (signature_string, webhook_verification, etc.)
            result: Result of verification (for webhook)
        """
        if not self.env.context.get('telebirr_debug', False):
            return  # Skip logging unless debug mode
        
        log_data = {
            'process_type': process_type,
            'data': data,
            'timestamp': self.env.cr.now,
        }
        
        if result is not None:
            log_data['result'] = result
        
        # Log to system logger
        from odoo.tools import logging
        logger = logging.getLogger(__name__)
        logger.info(f"Telebirr Signature Process: {json.dumps(log_data)}")
    
    @api.model
    def validate_key_format(self, key_string, key_type='private'):
        """
        Validate RSA key format
        
        Args:
            key_string: RSA key string to validate
            key_type: 'private' or 'public'
            
        Returns:
            Tuple (is_valid, error_message)
        """
        try:
            if not key_string:
                return False, "Key cannot be empty"
            
            # Try to import the key
            if '-----BEGIN' in key_string:
                key_bytes = key_string.encode('utf-8')
            else:
                key_bytes = base64.b64decode(key_string)
            
            rsa_key = RSA.importKey(key_bytes)
            
            # Validate key type
            if key_type == 'private' and not hasattr(rsa_key, 'd'):
                return False, "Provided key is not a private key"
            elif key_type == 'public' and hasattr(rsa_key, 'd'):
                return False, "Provided key is not a public key"
            
            # Check key size (minimum 2048 bits)
            key_size = rsa_key.size_in_bits()
            if key_size < 2048:
                return False, f"Key size {key_size} is too small (minimum 2048 bits)"
            
            return True, ""
            
        except Exception as e:
            return False, f"Invalid key format: {str(e)}"
    
    @api.model
    def format_key_for_display(self, key_string):
        """
        Format RSA key for secure display in UI
        
        Args:
            key_string: RSA key string
            
        Returns:
            Formatted key string (truncated for security)
        """
        if not key_string:
            return ''
        
        # Show only first 30 and last 30 characters
        if len(key_string) > 100:
            return f"{key_string[:30]}...{key_string[-30:]}"
        
        return key_string
    
    @api.model
    def generate_test_signature(self):
        """
        Generate test signature for development
        
        Returns:
            Dictionary with test data and signature
        """
        test_data = {
            'timestamp': '1234567890',
            'nonce_str': 'test123456',
            'method': 'payment.preorder',
            'version': '1.0',
            'biz_content': {
                'appid': 'test_app_123',
                'merch_code': '123456',
                'merch_order_id': 'TEST123456789',
                'total_amount': '100.00',
                'trans_currency': 'ETB'
            }
        }
        
        # Generate test private key
        test_private_key = RSA.generate(2048, Random.new().read)
        test_private_key_pem = test_private_key.exportKey().decode('utf-8')
        
        # Sign test data
        signature = self.sign_request(test_data, test_private_key_pem)
        
        return {
            'test_data': test_data,
            'signature': signature,
            'private_key': test_private_key_pem,
            'public_key': test_private_key.publickey().exportKey().decode('utf-8')
        }
