// Vercel Serverless Function: GET /api/paddle-config.js
//
// Supplies the Paddle LIVE client-side token to the browser at request
// time. The value is read from the PADDLE_CLIENT_TOKEN environment
// variable configured directly in the Vercel project (Project Settings
// > Environment Variables) -- it is never committed to this repository.
//
// A Paddle client-side token is designed by Paddle to be safe in
// frontend code (see
// https://developer.paddle.com/build/transactions/user-client-tokens);
// it is not a secret API key. This function does not read, hold, or
// expose any server-side Paddle API key/secret.
module.exports = (req, res) => {
  const token = process.env.PADDLE_CLIENT_TOKEN || "";
  res.setHeader("Content-Type", "application/javascript; charset=utf-8");
  res.setHeader("Cache-Control", "no-store");
  res.status(200).send("window.PADDLE_CLIENT_TOKEN = " + JSON.stringify(token) + ";");
};
