# One-tap capture — setup

## What this solves

Walmart, Home Depot, Amazon and Target block your app's server from reading
their pages. They don't block you. When you open one of those pages on your
phone, it loads fine.

So your phone reads the page, and hands it to the app.

## What you do, once

**Step 1 — create the table**

Open Supabase → SQL Editor → paste the contents of `supabase_import_inbox.sql`
→ Run. You only ever do this once.

**Step 2 — get your line**

In eBay io: **Products → Import → Set up one-tap capture**.

There's one line of code there. Copy it. You don't need to understand it, edit
it, or add anything to it — the app already filled in everything it needs.

**Step 3 — save it as a bookmark**

*iPhone:*
1. In Safari, bookmark any page at all (Share → Add Bookmark). Name it **eBay io**.
2. Tap the bookmarks icon → Edit → tap that bookmark.
3. Clear the address line, paste your copied line there, save.

*Computer:*
Create a new bookmark named **eBay io**, paste the copied line as the address.

## What you do from then on

1. Open any product page — Walmart, Home Depot, anywhere.
2. Tap your **eBay io** bookmark. It says "Sent to eBay io."
3. Open the app → Products → press **Import capture**.

The product comes in with title, price, images and description, and gets the
same SEO pass and eBay formatting as a normal import.

## About your data

You asked that Supabase never hold items. It doesn't.

- The captured page is deleted the moment you press Import.
- Anything you capture and never import is thrown away after 15 minutes.
- Only the raw page passes through — never a product, never a draft.
- Normally that table is empty.

## Why not fully automatic?

The only way to make the server do this is to make Walmart believe a server is
a person. That breaks every time they update their systems, and it puts your
business on the wrong side of their terms while you're selling this to clients.

This way works because nothing is being tricked — you really are a person
looking at the page.

## If something doesn't work

- **Button says nothing is waiting:** make sure you tapped the bookmark and saw
  the confirmation first.
- **"Capture failed":** the table probably isn't created yet — redo Step 1.
- **Nothing extracted:** the page may not be a product page. Try the product
  page itself rather than a search or category page.
