#!/bin/bash
# setup.sh — One-time setup for the trading agent
# Run this once after cloning the project.

set -e

echo "🤖 Trading Agent Setup"
echo "======================"

# 1. Python dependencies
echo ""
echo "→ Installing Python dependencies..."
pip install requests python-dotenv --quiet

# 2. Create .env file if it doesn't exist
if [ ! -f .env ]; then
  echo ""
  echo "→ Creating .env file..."
  cat > .env << 'EOF'
# Alpaca Paper Trading Credentials
# Get these from https://app.alpaca.markets → API Keys (make sure Paper Trading is selected)
APCA_API_KEY_ID=your_paper_key_here
APCA_API_SECRET_KEY=your_paper_secret_here
APCA_BASE_URL=https://paper-api.alpaca.markets
EOF
  echo "  ✅ .env created — open it and add your Alpaca paper trading keys"
else
  echo "  ⚠️  .env already exists — skipping"
fi

# 3. Create journal directory
mkdir -p journal
echo "→ Created journal/ directory"

# 4. Verify structure
echo ""
echo "→ Project structure:"
find . -not -path './.git/*' -not -path './journal/*' | sort | head -30

echo ""
echo "✅ Setup complete!"
echo ""
echo "Next steps:"
echo "  1. Edit .env with your Alpaca paper trading API keys"
echo "     → Get them at: https://app.alpaca.markets (toggle to Paper Trading)"
echo ""
echo "  2. Test your connection:"
echo "     python scripts/research.py account"
echo ""
echo "  3. Run a manual research pass:"
echo "     python scripts/research.py full"
echo ""
echo "  4. Check market status:"
echo "     python scripts/trade.py status"
echo ""
echo "  5. Configure Claude Code routines:"
echo "     → Open .claude/routines.json"
echo "     → Enable in Claude Code settings"
echo ""
echo "  6. Run your first manual end-of-day journal:"
echo "     python scripts/journal.py eod"
