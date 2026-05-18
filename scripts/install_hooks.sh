#!/bin/bash
# Git hook'larni o'rnatish
# Ishlatish: bash scripts/install_hooks.sh

HOOKS_DIR=".git/hooks"
mkdir -p "$HOOKS_DIR"

cat > "$HOOKS_DIR/pre-push" << 'EOF'
#!/bin/bash
# Pre-push hook: testlar o'tsa push, o'tmasa to'xtatadi

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Pre-push: testlar ishga tushmoqda..."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Xavfsizlik tekshiruvi: pjsip.conf da hardcoded credentials
if grep -qE "^(username|password)\s*=\s*[a-zA-Z0-9]{4,}$" config/asterisk/pjsip.conf 2>/dev/null; then
    echo ""
    echo "❌ XATO: config/asterisk/pjsip.conf da HARDCODED credentials!"
    echo "   ENV var ishlatilishi kerak: username=\${ENV(SIP_USERNAME)}"
    echo ""
    exit 1
fi

# Python testlari
EXTERNAL_API_SECRET="ci-test-secret-minimum-32chars" \
WEBHOOK_SECRET="ci-test-secret-minimum-32chars" \
python -m pytest tests/ -q --tb=short 2>&1

EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
    echo ""
    echo "✅ Barcha testlar o'tdi — push davom etmoqda"
    echo ""
    exit 0
else
    echo ""
    echo "❌ Testlar muvaffaqiyatsiz — push TO'XTATILDI"
    echo "   Xatolarni tuzating va qayta urinib ko'ring"
    echo ""
    exit 1
fi
EOF

chmod +x "$HOOKS_DIR/pre-push"
echo "✅ Pre-push hook o'rnatildi: $HOOKS_DIR/pre-push"
echo "   Endi har push dan oldin testlar avtomatik ishga tushadi."
