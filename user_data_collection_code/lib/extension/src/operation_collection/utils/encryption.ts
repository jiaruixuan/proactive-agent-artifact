import * as crypto from "crypto";


const PUBLIC_KEY = `-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAuqKd7ucEexpCj3PenMt1
tovkxwRuZ17xBy7RC39VwZgbCFWlrhJE9vEACM8HqhW8PjVSzbfM258iJMmpyCsV
t2SrWXmQgGiBi+zVCkJIgUS5iZLgoCCCUpSXKTdzTIpuSQeCHpyvMsqCVOq2CH9b
CFlopMVFfqebwp6qnYMQY+YH0FvbRmbinP2P6Rm+ed1kraB3oaeZQ71imuXyjApL
rJ1aClHbv5SP0ESYFY6pSTkqR6gezGNpkgD+R1qlBpEMNwAyEty2em6CGYybXCO4
bBzrttc/4637TNtPkcFSO9nWMqRwHqYvlck29EH5jLm9vRHQRlQ3wtL8MEsIQ6sD
VwIDAQAB
-----END PUBLIC KEY-----`;

function encryptAESKey(aesKey: Buffer): string {
	const encrypted = crypto.publicEncrypt(
		{
			key: PUBLIC_KEY,
			padding: crypto.constants.RSA_PKCS1_OAEP_PADDING,
			oaepHash: "sha256",
		},
		aesKey
	);
	return encrypted.toString("base64");
}

export function encryptData(data: string): string {
	const aesKey = crypto.randomBytes(32); // 256 bits
	const iv = crypto.randomBytes(16); // 128 bits (AES block size)

	const cipher = crypto.createCipheriv("aes-256-cbc", aesKey, iv);
	let encrypted = cipher.update(data, "utf8");
	encrypted = Buffer.concat([encrypted, cipher.final()]);

	const encryptedKey = encryptAESKey(aesKey);

	const ivBase64 = iv.toString("base64");
	const encryptedBase64 = encrypted.toString("base64");

	return `${encryptedKey}:${ivBase64}:${encryptedBase64}`;
}

export function encryptJSON(obj: unknown): string {
	const jsonString = JSON.stringify(obj);
	return encryptData(jsonString);
}
