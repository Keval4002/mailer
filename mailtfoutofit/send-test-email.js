require("dotenv").config();

const nodemailer = require("nodemailer");

const requiredEnvVars = [
  "SMTP_HOST",
  "SMTP_PORT",
  "SMTP_SECURE",
  "SMTP_USER",
  "SMTP_PASS",
  "MAIL_FROM",
  "MAIL_TO"
];

const missingEnvVars = requiredEnvVars.filter((name) => !process.env[name]);

if (missingEnvVars.length > 0) {
  console.error(
    `Missing required environment variables: ${missingEnvVars.join(", ")}`
  );
  process.exit(1);
}

const smtpPort = Number(process.env.SMTP_PORT);
const smtpSecure = process.env.SMTP_SECURE === "true";

if (Number.isNaN(smtpPort)) {
  console.error("SMTP_PORT must be a valid number.");
  process.exit(1);
}

async function main() {
  const transporter = nodemailer.createTransport({
    host: process.env.SMTP_HOST,
    port: smtpPort,
    secure: smtpSecure,
    auth: {
      user: process.env.SMTP_USER,
      pass: process.env.SMTP_PASS
    }
  });

  await transporter.verify();

  const info = await transporter.sendMail({
    from: process.env.MAIL_FROM,
    to: process.env.MAIL_TO,
    subject: "Dummy email from Nodemailer test",
    text: [
      "This is a test email sent from your local Node.js script.",
      "",
      `Sent at: ${new Date().toISOString()}`
    ].join("\n"),
    html: `
      <p>This is a test email sent from your local Node.js script.</p>
      <p><strong>Sent at:</strong> ${new Date().toISOString()}</p>
    `
  });

  console.log("SMTP connection verified.");
  console.log(`Email sent successfully. Message ID: ${info.messageId}`);
}

main().catch((error) => {
  console.error("Failed to send test email.");
  console.error(error.message);

  if (
    error.message.includes("Username and Password not accepted") ||
    error.message.includes("Invalid login")
  ) {
    console.error(
      "If you are using Gmail, use a 16-character App Password instead of your normal Gmail password."
    );
  }

  process.exit(1);
});
