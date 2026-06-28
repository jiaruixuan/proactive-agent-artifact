/**
 * Sanitization helper tests.
 * 
 * Run with: node -r ts-node/register sanitize.test.ts
 */

import { sanitizeObject, sanitizeString, sanitizeError } from './sanitize';

// Test 1: sensitive fields are removed.
console.log('\n=== Test 1: sensitive fields are removed ===');
const testData1 = {
	operationId: 123,
	userId: 'user123456789',
	token: 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0',
	sessionId: '550e8400-e29b-41d4-a716-446655440000',
	count: 50,
	timestamp: 1642524000000,
};

const sanitized1 = sanitizeObject(testData1);
console.log('Original data:', testData1);
console.log('Sanitized data:', sanitized1);
console.log('✓ userId removed:', !('userId' in sanitized1));
console.log('✓ token removed:', !('token' in sanitized1));
console.log('✓ sessionId removed:', !('sessionId' in sanitized1));
console.log('✓ non-sensitive field retained:', sanitized1.operationId === 123);

// Test 2: sensitive data inside strings is redacted.
console.log('\n=== Test 2: sensitive data inside strings is redacted ===');
const testString = 'User user123456789 logged in with token eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0 and sessionId 550e8400-e29b-41d4-a716-446655440000';
const sanitizedString = sanitizeString(testString);
console.log('Original string:', testString);
console.log('Sanitized string:', sanitizedString);
console.log('✓ full userId removed:', !sanitizedString.includes('user123456789'));
console.log('✓ full token removed:', !sanitizedString.includes('eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9'));
console.log('✓ full sessionId removed:', !sanitizedString.includes('550e8400-e29b-41d4-a716-446655440000'));

// Test 3: nested objects are sanitized.
console.log('\n=== Test 3: nested objects are sanitized ===');
const testData3 = {
	user: {
		userId: 'user123',
		name: 'John',
		auth: {
			token: 'secret_token_123',
			sessionId: '550e8400-e29b-41d4-a716-446655440000',
		},
	},
	metadata: {
		count: 10,
		timestamp: Date.now(),
	},
};

const sanitized3 = sanitizeObject(testData3);
console.log('Original data:', JSON.stringify(testData3, null, 2));
console.log('Sanitized data:', JSON.stringify(sanitized3, null, 2));
console.log('✓ nested userId removed:', !('userId' in (sanitized3.user || {})));
console.log('✓ nested token removed:', !('token' in (sanitized3.user?.auth || {})));
console.log('✓ non-sensitive field retained:', sanitized3.user?.name === 'John');

// Test 4: Error objects are sanitized.
console.log('\n=== Test 4: Error objects are sanitized ===');
const testError = new Error('Connection failed with token eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.abc123');
const sanitizedError = sanitizeError(testError);
console.log('Original error:', testError.message);
console.log('Sanitized error:', sanitizedError.message);
console.log('✓ full token removed:', !sanitizedError.message.includes('eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.abc123'));
console.log('✓ partial token retained:', sanitizedError.message.includes('eyJhbG***'));

// Test 5: file paths are shortened.
console.log('\n=== Test 5: file paths are shortened ===');
const testPath = '/workspace/project/lib/extension/src/CloudStorage.ts';
const sanitizedPath = sanitizeString(testPath);
console.log('Original path:', testPath);
console.log('Sanitized path:', sanitizedPath);
console.log('✓ path shortened:', sanitizedPath.length < testPath.length);
console.log('✓ ellipsis retained:', sanitizedPath.includes('...'));

console.log('\n=== All tests completed ===\n');
