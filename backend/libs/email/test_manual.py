"""
Manual test script for email service.

Usage:
    python test_manual.py

Ensure .env is configured with SMTP settings before running.
"""

import asyncio
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from libs.email import EmailManager
from libs.log_manager.controller import LoggingController


async def test_simple_email():
    """Test sending a simple HTML email."""
    print("=" * 60)
    print("Test 1: Simple Email")
    print("=" * 60)

    try:
        email = EmailManager()

        # Verify configuration first
        print("\n1. Verifying email configuration...")
        is_valid = await email.verify_configuration()

        if not is_valid:
            print("❌ Email configuration verification failed")
            print("   Check your SMTP credentials in .env")
            return False

        print("✅ Email configuration verified")

        # Send test email
        print("\n2. Sending simple test email...")
        success = await email.send_simple(
            to_email=input("Enter recipient email: "),
            subject="Yoru Email Service Test",
            html_body="""
                <h1>Email Service Test</h1>
                <p>If you're reading this, the email service is working!</p>
                <p><strong>Features tested:</strong></p>
                <ul>
                    <li>SMTP connection</li>
                    <li>HTML email rendering</li>
                    <li>Async email sending</li>
                </ul>
            """,
        )

        if success:
            print("✅ Simple email sent successfully")
            print("   Check the recipient's inbox")
            return True
        else:
            print("❌ Failed to send simple email")
            return False

    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback

        traceback.print_exc()
        return False


async def test_templated_email():
    """Test sending templated email."""
    print("\n" + "=" * 60)
    print("Test 2: Templated Email")
    print("=" * 60)

    try:
        email = EmailManager()

        print("\n1. Sending invitation template...")
        to_email = input("Enter recipient email: ")

        success = await email.send_template(
            template_name="invitation.html",
            to_email=to_email,
            subject="You've been invited to join Yoru",
            context={
                "inviter_name": "Test User",
                "inviter_email": "test@example.com",
                "invite_url": "http://localhost:3000/accept-invite?token=test123",
                "expires_at": "December 31, 2024",
                "message": "This is a test invitation from the email service!",
            },
        )

        if success:
            print("✅ Invitation email sent successfully")
            print("   Check the recipient's inbox")
            return True
        else:
            print("❌ Failed to send invitation email")
            return False

    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback

        traceback.print_exc()
        return False


async def test_all_templates():
    """Test all available templates."""
    print("\n" + "=" * 60)
    print("Test 3: All Templates")
    print("=" * 60)

    try:
        email = EmailManager()
        to_email = input("Enter recipient email: ")

        templates = [
            {
                "name": "invitation.html",
                "subject": "Test: User Invitation",
                "context": {
                    "inviter_name": "John Doe",
                    "inviter_email": "john@example.com",
                    "invite_url": "http://localhost:3000/accept-invite?token=abc123",
                    "expires_at": "December 31, 2024",
                    "message": "Please join our team!",
                },
            },
            {
                "name": "organization_invitation.html",
                "subject": "Test: Organization Invitation",
                "context": {
                    "org_name": "Test Organization",
                    "inviter_name": "Jane Smith",
                    "role": "member",
                    "invite_url": "http://localhost:3000/accept-org-invite?token=xyz789",
                },
            },
            {
                "name": "signup_confirmation.html",
                "subject": "Test: Welcome Email",
                "context": {
                    "user_name": "Alice",
                    "user_email": to_email,
                    "dashboard_url": "http://localhost:3000/dashboard",
                },
            },
        ]

        for i, template in enumerate(templates, 1):
            print(f"\n{i}. Testing {template['name']}...")
            success = await email.send_template(
                template_name=template["name"],
                to_email=to_email,
                subject=template["subject"],
                context=template["context"],
            )

            if success:
                print(f"   ✅ {template['name']} sent successfully")
            else:
                print(f"   ❌ Failed to send {template['name']}")
                return False

            # Small delay between emails
            await asyncio.sleep(1)

        print("\n✅ All templates tested successfully")
        print(f"   Check {to_email} inbox for 3 test emails")
        return True

    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback

        traceback.print_exc()
        return False


async def main():
    """Run all tests."""
    print("\n")
    print("╔" + "=" * 58 + "╗")
    print("║" + " " * 12 + "Yoru Email Service Test" + " " * 17 + "║")
    print("╚" + "=" * 58 + "╝")
    print()

    tests = [
        ("Verify Configuration & Simple Email", test_simple_email),
        ("Templated Email", test_templated_email),
        ("All Templates", test_all_templates),
    ]

    print("Available tests:")
    for i, (name, _) in enumerate(tests, 1):
        print(f"{i}. {name}")
    print("4. Run all tests")
    print()

    choice = input("Select test (1-4): ").strip()

    if choice == "4":
        # Run all tests
        results = []
        for name, test_func in tests:
            print(f"\n\n{'=' * 60}")
            print(f"Running: {name}")
            print("=" * 60)
            result = await test_func()
            results.append((name, result))

        # Summary
        print("\n\n" + "=" * 60)
        print("Test Summary")
        print("=" * 60)
        for name, result in results:
            status = "✅ PASS" if result else "❌ FAIL"
            print(f"{status} - {name}")

    elif choice in ["1", "2", "3"]:
        idx = int(choice) - 1
        name, test_func = tests[idx]
        await test_func()

    else:
        print("Invalid choice")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\nTest interrupted by user")
    except Exception as e:
        print(f"\n\nFatal error: {e}")
        import traceback

        traceback.print_exc()
